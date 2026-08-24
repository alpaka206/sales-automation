"""Store HubSpot Conversations delivery identifiers on outbound messages."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "messages" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("messages")}
    with engine.begin() as conn:
        if "hubspot_thread_id" not in columns:
            conn.execute(text("ALTER TABLE messages ADD COLUMN hubspot_thread_id VARCHAR(64)"))
        if "hubspot_message_id" not in columns:
            conn.execute(text("ALTER TABLE messages ADD COLUMN hubspot_message_id VARCHAR(128)"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_messages_hubspot_message_id "
                "ON messages (hubspot_message_id)"
            )
        )
    logger.info("0088: added HubSpot Conversations thread/message delivery identifiers")
