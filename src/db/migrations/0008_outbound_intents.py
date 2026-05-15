"""Add outbound_intents table for natural language intent routing."""

from __future__ import annotations

from sqlalchemy import Engine, text


def up(engine: Engine) -> None:
    """Create the outbound_intents table."""
    dialect = engine.dialect.name
    if dialect == "sqlite":
        pk = "INTEGER PRIMARY KEY AUTOINCREMENT"
    else:
        pk = "SERIAL PRIMARY KEY"

    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS outbound_intents (
                id {pk},
                user_query TEXT NOT NULL,
                routed_source TEXT NOT NULL,
                routed_filters JSON,
                confidence REAL NOT NULL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'pending_user_input',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
