"""Defensive, idempotent backfill of `messages` columns that exist in the ORM
model but were never added by an incremental ALTER migration (they reached
existing DBs only via 0001's create_all snapshot).

This is a NO-OP on any DB that already has these columns (every DB created by the
current 0001 create_all, and the live Supabase). It only matters for a DB created
at an intermediate point in history. Adds only nullable / defaulted columns so it
can never fail on a populated table. Works on SQLite and Postgres.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

# (column, DDL) — only nullable or defaulted columns (safe to ADD on a populated table).
_COLUMNS: list[tuple[str, str]] = [
    ("channel", "VARCHAR NOT NULL DEFAULT 'email'"),
    ("from_address", "VARCHAR"),
    ("to_address", "VARCHAR"),
    ("subject", "VARCHAR"),
    ("language", "VARCHAR NOT NULL DEFAULT 'ko'"),
    ("score_snapshot", "INTEGER"),
    ("prompt_variant", "VARCHAR"),
    ("draft_provider", "VARCHAR"),
    ("approved_by", "VARCHAR"),
    ("approved_at", "TIMESTAMP"),
    ("sent_at", "TIMESTAMP"),
]


def up(engine: Engine) -> None:
    insp = inspect(engine)
    if "messages" not in insp.get_table_names():
        return

    existing = {c["name"] for c in insp.get_columns("messages")}
    is_sqlite = engine.dialect.name == "sqlite"
    bool_default = "0" if is_sqlite else "false"

    to_add = list(_COLUMNS)
    # `replied BOOLEAN NOT NULL DEFAULT <dialect>` — dialect-specific literal.
    to_add.append(("replied", f"BOOLEAN NOT NULL DEFAULT {bool_default}"))

    with engine.begin() as conn:
        for column, ddl in to_add:
            if column in existing:
                continue
            conn.execute(text(f"ALTER TABLE messages ADD COLUMN {column} {ddl}"))
            logger.info("0017: added messages.%s", column)
    logger.info("0017: messages columns ensured.")
