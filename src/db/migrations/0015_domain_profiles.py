"""Create domain_profiles table for caching company analysis results per email domain."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    insp = inspect(engine)
    if "domain_profiles" not in insp.get_table_names():
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE domain_profiles (
                        domain VARCHAR(255) PRIMARY KEY,
                        company_name VARCHAR(255),
                        industry VARCHAR(128),
                        services TEXT,
                        target_market VARCHAR(128),
                        size_hint VARCHAR(64),
                        confidence VARCHAR(16) NOT NULL,
                        source VARCHAR(32) NOT NULL,
                        homepage_title TEXT,
                        homepage_description TEXT,
                        homepage_fetch_status VARCHAR(32),
                        notes TEXT,
                        analyzed_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
            )
            logger.info("Created domain_profiles table.")
    else:
        logger.info("domain_profiles table already exists, skipping create.")

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_domain_profiles_industry "
                "ON domain_profiles (industry)"
            )
        )
    logger.info("0015: domain_profiles ready.")
