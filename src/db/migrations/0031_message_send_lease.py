"""Add an expiring lease to claimed outbound messages."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def up(engine: Engine) -> None:
    if "messages" not in set(inspect(engine).get_table_names()):
        return
    columns = {item["name"] for item in inspect(engine).get_columns("messages")}
    with engine.begin() as conn:
        if "send_claimed_at" not in columns:
            conn.execute(text("ALTER TABLE messages ADD COLUMN send_claimed_at TIMESTAMP"))
        if "post_send_synced_at" not in columns:
            conn.execute(text("ALTER TABLE messages ADD COLUMN post_send_synced_at TIMESTAMP"))
        if "post_send_sync_attempted_at" not in columns:
            conn.execute(text("ALTER TABLE messages ADD COLUMN post_send_sync_attempted_at TIMESTAMP"))
        if "post_send_sync_attempts" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE messages ADD COLUMN "
                    "post_send_sync_attempts INTEGER NOT NULL DEFAULT 0"
                )
            )
        if "post_send_sync_error" not in columns:
            conn.execute(text("ALTER TABLE messages ADD COLUMN post_send_sync_error TEXT"))
        # Give claims created by the previous release a full lease. This prevents
        # a rolling deployment from immediately duplicating an in-flight email.
        conn.execute(
            text(
                "UPDATE messages SET send_claimed_at = CURRENT_TIMESTAMP "
                "WHERE status LIKE 'sending:%' AND send_claimed_at IS NULL"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_messages_status_claimed "
                "ON messages (status, send_claimed_at)"
            )
        )
        # Old deliveries predate sync tracking and must not create a retry storm.
        conn.execute(
            text(
                "UPDATE messages SET post_send_synced_at = COALESCE(sent_at, CURRENT_TIMESTAMP) "
                "WHERE status = 'sent' AND post_send_synced_at IS NULL"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_messages_post_send_sync "
                "ON messages (status, post_send_synced_at, post_send_sync_attempted_at)"
            )
        )
