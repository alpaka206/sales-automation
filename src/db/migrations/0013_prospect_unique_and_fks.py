"""Enforce uniqueness on prospects.normalized_email and add (domain, full_name) secondary index.

This migration is conservative on SQLite (no FK rebuilds — those are model-level only
and applied on fresh DBs). On Postgres we attach a UNIQUE constraint to the existing column
and reuse an idempotent CREATE INDEX IF NOT EXISTS for the secondary key.

Duplicate handling:
    Before adding UNIQUE, we collapse duplicates by keeping the lowest-id prospect per
    normalized_email and clearing normalized_email on the duplicates (NULLs are allowed
    by the column). The original rows stay, so no data is lost.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def _dedupe_normalized_email(conn) -> int:
    """Null out normalized_email on duplicate rows, keeping the lowest id. Returns affected count."""
    rows = conn.execute(
        text(
            """
            SELECT id, normalized_email
            FROM prospects
            WHERE normalized_email IS NOT NULL
            ORDER BY normalized_email, id
            """
        )
    ).fetchall()

    seen: set[str] = set()
    dup_ids: list[int] = []
    for row in rows:
        rid, email = row[0], row[1]
        if email in seen:
            dup_ids.append(rid)
        else:
            seen.add(email)

    if not dup_ids:
        return 0

    id_list = ",".join(str(int(i)) for i in dup_ids)
    conn.execute(
        text(f"UPDATE prospects SET normalized_email = NULL WHERE id IN ({id_list})")
    )
    return len(dup_ids)


def up(engine: Engine) -> None:
    insp = inspect(engine)
    if "prospects" not in insp.get_table_names():
        logger.warning("prospects table missing; skipping 0013.")
        return

    dialect = engine.dialect.name
    with engine.begin() as conn:
        dropped = _dedupe_normalized_email(conn)
        if dropped:
            logger.info("Cleared normalized_email on %d duplicate prospect rows.", dropped)

        if dialect == "postgresql":
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_prospects_normalized_email "
                    "ON prospects (normalized_email) WHERE normalized_email IS NOT NULL"
                )
            )
        else:
            # SQLite: regular unique index (treats NULLs as distinct, which is what we want)
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_prospects_normalized_email "
                    "ON prospects (normalized_email)"
                )
            )

        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_prospects_domain_fullname "
                "ON prospects (domain, full_name)"
            )
        )

    logger.info("0013: prospects unique index + (domain, full_name) index applied.")
