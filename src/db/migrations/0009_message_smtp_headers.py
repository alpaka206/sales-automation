"""Add smtp_message_id and in_reply_to columns to messages table."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    """Add SMTP header fields for reply detection."""
    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns("messages")}

    with engine.begin() as conn:
        if "smtp_message_id" not in existing:
            conn.execute(text("ALTER TABLE messages ADD COLUMN smtp_message_id VARCHAR"))
            logger.info("Added smtp_message_id column.")

        if "in_reply_to" not in existing:
            conn.execute(text("ALTER TABLE messages ADD COLUMN in_reply_to VARCHAR"))
            logger.info("Added in_reply_to column.")
