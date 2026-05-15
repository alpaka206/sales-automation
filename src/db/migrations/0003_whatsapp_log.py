"""Add WhatsApp delivery tracking columns to messages table."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def up(engine: Engine) -> None:
    """Add whatsapp_attempted, whatsapp_sent, whatsapp_error columns."""
    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns("messages")}
    is_sqlite = engine.dialect.name == "sqlite"
    false_literal = "0" if is_sqlite else "false"

    with engine.begin() as conn:
        if "whatsapp_attempted" not in existing:
            conn.execute(text(
                f"ALTER TABLE messages ADD COLUMN whatsapp_attempted BOOLEAN DEFAULT {false_literal} NOT NULL"
            ))
        if "whatsapp_sent" not in existing:
            conn.execute(text(
                f"ALTER TABLE messages ADD COLUMN whatsapp_sent BOOLEAN DEFAULT {false_literal} NOT NULL"
            ))
        if "whatsapp_error" not in existing:
            conn.execute(text("ALTER TABLE messages ADD COLUMN whatsapp_error TEXT"))
