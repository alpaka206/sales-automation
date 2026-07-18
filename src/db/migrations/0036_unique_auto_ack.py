"""Guarantee at most one automatic acknowledgement per conversation."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


_INDEX = "ux_messages_one_auto_ack_per_conversation"


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "messages" not in set(inspector.get_table_names()):
        return
    if _INDEX in {item["name"] for item in inspector.get_indexes("messages")}:
        return
    # Deliberately fail if historical duplicates exist: silently deleting sent
    # email audit rows would be worse than stopping the deployment for review.
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX ux_messages_one_auto_ack_per_conversation "
                "ON messages (conversation_id) WHERE prompt_variant = 'auto_ack'"
            )
        )
