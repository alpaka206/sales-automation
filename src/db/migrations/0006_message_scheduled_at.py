"""Add scheduled_at column and composite index to messages table."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def up(engine: Engine) -> None:
    """Add messages.scheduled_at and index (status, scheduled_at)."""
    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns("messages")}

    with engine.begin() as conn:
        if "scheduled_at" not in existing:
            conn.execute(text("ALTER TABLE messages ADD COLUMN scheduled_at TIMESTAMP"))

        existing_indexes = {idx["name"] for idx in insp.get_indexes("messages")}
        if "ix_messages_status_scheduled" not in existing_indexes:
            conn.execute(text(
                "CREATE INDEX ix_messages_status_scheduled ON messages (status, scheduled_at)"
            ))
