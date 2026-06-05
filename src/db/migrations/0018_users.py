"""Create users table for Google-OAuth web-UI access + allowlist + edit attribution."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    insp = inspect(engine)
    if "users" not in insp.get_table_names():
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE users (
                        email VARCHAR(320) PRIMARY KEY,
                        name VARCHAR(255),
                        picture TEXT,
                        role VARCHAR(16) NOT NULL DEFAULT 'member',
                        approved BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMP NOT NULL,
                        last_login_at TIMESTAMP
                    )
                    """
                )
            )
            logger.info("Created users table.")
    else:
        logger.info("users table already exists, skipping create.")
    logger.info("0018: users ready.")
