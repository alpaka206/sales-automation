"""Drop the outbound agent's tables — the Outbound feature was removed entirely.

The outbound agent (prospecting: YouTube/LinkedIn/CSV → opening emails, ICP
scoring, follow-ups) and all its code were deleted. This migration removes its
tables so the schema matches the models:

- ``prospects`` — discovered leads. ``conversations.prospect_id`` had a FK to it;
  on Postgres we DROP ... CASCADE so that FK constraint goes too (the column
  itself is kept as a plain nullable integer, always NULL for inbound threads).
- ``icp_rules`` / ``outbound_intents`` / ``country_send_windows`` — standalone
  outbound tables with no inbound dependents.

Idempotent (DROP TABLE IF EXISTS). Destructive: any existing prospect data is
gone. Works on SQLite (FKs unenforced) and Postgres (CASCADE).
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)

_TABLES = ("prospects", "icp_rules", "outbound_intents", "country_send_windows")


def up(engine: Engine) -> None:
    cascade = " CASCADE" if engine.dialect.name == "postgresql" else ""
    with engine.begin() as conn:
        for table in _TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {table}{cascade}"))
            logger.info("0023: dropped table %s", table)
    logger.info("0023: outbound tables removed.")
