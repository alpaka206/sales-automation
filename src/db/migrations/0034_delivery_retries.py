"""Track bounded auto-ack and Slack delivery retries on message rows."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def up(engine: Engine) -> None:
    if "messages" not in set(inspect(engine).get_table_names()):
        return
    columns = {item["name"] for item in inspect(engine).get_columns("messages")}
    with engine.begin() as conn:
        if "send_attempts" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE messages ADD COLUMN "
                    "send_attempts INTEGER NOT NULL DEFAULT 0"
                )
            )
        if "slack_notification_attempted_at" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE messages ADD COLUMN "
                    "slack_notification_attempted_at TIMESTAMP"
                )
            )
        if "slack_notification_attempts" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE messages ADD COLUMN "
                    "slack_notification_attempts INTEGER NOT NULL DEFAULT 0"
                )
            )
