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

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE icp_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source VARCHAR NOT NULL UNIQUE,
                criteria_md TEXT NOT NULL DEFAULT '',
                enabled BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT (datetime('now')),
                updated_at DATETIME NOT NULL DEFAULT (datetime('now'))
            )
        """))
        logger.info("Created icp_rules table.")
