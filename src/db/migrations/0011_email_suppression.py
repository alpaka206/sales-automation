"""Create email_suppression table for unsubscribe/bounce/complaint tracking."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    """Create the email_suppression table if it doesn't exist."""
    insp = inspect(engine)
    if "email_suppression" in insp.get_table_names():
        logger.info("email_suppression table already exists, skipping.")
        return

    is_sqlite = engine.dialect.name == "sqlite"
    ts_default = "(datetime('now'))" if is_sqlite else "now()"

    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE email_suppression (
                email VARCHAR NOT NULL PRIMARY KEY,
                reason VARCHAR NOT NULL DEFAULT 'unsubscribe',
                created_at TIMESTAMP NOT NULL DEFAULT {ts_default}
            )
        """))
        logger.info("Created email_suppression table.")
