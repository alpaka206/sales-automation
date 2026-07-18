"""Add the durable inbound queue."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    is_sqlite = engine.dialect.name == "sqlite"
    id_column = (
        "INTEGER PRIMARY KEY AUTOINCREMENT"
        if is_sqlite
        else "INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY"
    )
    timestamp = "CURRENT_TIMESTAMP" if is_sqlite else "now()"
    json_type = "TEXT" if is_sqlite else "JSON"

    with engine.begin() as conn:
        if "inbound_jobs" not in tables:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE inbound_jobs (
                        id {id_column},
                        event_key VARCHAR(255) NOT NULL UNIQUE,
                        source VARCHAR(32) NOT NULL,
                        payload {json_type} NOT NULL,
                        status VARCHAR(16) NOT NULL DEFAULT 'pending',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        available_at TIMESTAMP NOT NULL DEFAULT {timestamp},
                        locked_at TIMESTAMP,
                        completed_at TIMESTAMP,
                        last_error TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT {timestamp},
                        updated_at TIMESTAMP NOT NULL DEFAULT {timestamp}
                    )
                    """
                )
            )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_inbound_jobs_ready "
                "ON inbound_jobs (status, available_at)"
            )
        )
