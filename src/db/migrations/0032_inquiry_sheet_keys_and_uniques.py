"""Use stable per-inquiry sheet keys and enforce inbound dedup constraints."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        if "conversations" in tables:
            columns = {item["name"] for item in inspect(engine).get_columns("conversations")}
            if "sheet_client_id" not in columns:
                conn.execute(text("ALTER TABLE conversations ADD COLUMN sheet_client_id INTEGER"))
            if "contacts" in tables:
                conn.execute(
                    text(
                        "UPDATE conversations SET sheet_client_id = "
                        "(SELECT contacts.sheet_client_id FROM contacts "
                        "WHERE contacts.id = conversations.contact_id) "
                        "WHERE sheet_inbound_row IS NOT NULL AND sheet_client_id IS NULL"
                    )
                )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_conversations_sheet_client_id "
                    "ON conversations (sheet_client_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_conversations_hubspot_ticket_id "
                    "ON conversations (hubspot_ticket_id)"
                )
            )
        if "contacts" in tables:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_contacts_normalized_email "
                    "ON contacts (normalized_email)"
                )
            )
