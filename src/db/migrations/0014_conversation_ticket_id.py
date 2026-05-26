"""Add hubspot_ticket_id to conversations so each conv can be traced back to its
HubSpot Ticket. Indexed because we look up by ticket_id on every inbound ticket
webhook (to dedup / find existing conv)."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    insp = inspect(engine)
    if "conversations" not in insp.get_table_names():
        logger.warning("conversations table missing; skipping 0014.")
        return

    cols = {c["name"] for c in insp.get_columns("conversations")}
    with engine.begin() as conn:
        if "hubspot_ticket_id" not in cols:
            conn.execute(
                text("ALTER TABLE conversations ADD COLUMN hubspot_ticket_id VARCHAR(64)")
            )
            logger.info("Added conversations.hubspot_ticket_id column.")
        else:
            logger.info("conversations.hubspot_ticket_id already exists, skipping add.")

        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_conversations_hubspot_ticket_id "
                "ON conversations (hubspot_ticket_id)"
            )
        )
        logger.info("0014: conversations.hubspot_ticket_id ready.")
