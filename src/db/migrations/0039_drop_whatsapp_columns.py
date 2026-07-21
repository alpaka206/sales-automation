"""Drop the WhatsApp columns — the WhatsApp integration was removed entirely.

Removes the leftover delivery-tracking columns on ``messages`` (added in
0003_whatsapp_log) and the opt-in flag on ``contacts`` so the schema matches the
models. Column-guarded and idempotent: skips any column that is already absent, so
it is safe on a fresh DB (which never had them) and on Postgres/SQLite alike.

Destructive: the WhatsApp delivery flags are gone. They carried no data of value
(WhatsApp was best-effort metadata). ``contacts.phone`` is KEPT — it is general
contact info, not WhatsApp-specific.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

# (table, column) pairs left behind by the removed WhatsApp feature.
_COLUMNS = (
    ("messages", "whatsapp_attempted"),
    ("messages", "whatsapp_sent"),
    ("messages", "whatsapp_error"),
    ("contacts", "whatsapp_opt_in"),
)


def up(engine: Engine) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column in _COLUMNS:
            if table not in tables:
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if column not in cols:
                logger.info("0039: %s.%s already absent, skipping.", table, column)
                continue
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
            logger.info("0039: dropped %s.%s", table, column)
