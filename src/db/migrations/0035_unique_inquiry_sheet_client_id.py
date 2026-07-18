"""Enforce one stable Inbound DB Client ID per conversation."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def up(engine: Engine) -> None:
    if "conversations" not in set(inspect(engine).get_table_names()):
        return
    columns = {item["name"] for item in inspect(engine).get_columns("conversations")}
    if "sheet_client_id" not in columns:
        return

    with engine.begin() as conn:
        duplicates = conn.execute(
            text(
                "SELECT sheet_client_id, MIN(id) AS keep_id "
                "FROM conversations WHERE sheet_client_id IS NOT NULL "
                "GROUP BY sheet_client_id HAVING COUNT(*) > 1"
            )
        ).all()
        # Migration 0032 copied a contact-level ID onto legacy conversations.
        # Keep those conversations intact and clear only the ambiguous copied
        # key; a future sync can allocate a new per-inquiry stable ID.
        for client_id, keep_id in duplicates:
            conn.execute(
                text(
                    "UPDATE conversations SET sheet_client_id = NULL "
                    "WHERE sheet_client_id = :client_id AND id <> :keep_id"
                ),
                {"client_id": client_id, "keep_id": keep_id},
            )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_conversations_sheet_client_id "
                "ON conversations (sheet_client_id)"
            )
        )
