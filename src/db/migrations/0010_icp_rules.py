"""Create icp_rules table for per-source ICP scoring criteria."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    """Create the icp_rules table if it doesn't exist."""
    insp = inspect(engine)
    if "icp_rules" in insp.get_table_names():
        logger.info("icp_rules table already exists, skipping.")
        return

    is_sqlite = engine.dialect.name == "sqlite"
    pk_clause = "INTEGER PRIMARY KEY AUTOINCREMENT" if is_sqlite else "INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY"
    bool_true = "1" if is_sqlite else "TRUE"
    ts_default = "(datetime('now'))" if is_sqlite else "now()"

    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE icp_rules (
                id {pk_clause},
                source VARCHAR NOT NULL UNIQUE,
                criteria_md TEXT NOT NULL DEFAULT '',
                enabled BOOLEAN NOT NULL DEFAULT {bool_true},
                created_at TIMESTAMP NOT NULL DEFAULT {ts_default},
                updated_at TIMESTAMP NOT NULL DEFAULT {ts_default}
            )
        """))
        logger.info("Created icp_rules table.")
