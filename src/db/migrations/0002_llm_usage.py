"""Add llm_usage table for tracking LLM API call costs."""

from __future__ import annotations

from sqlalchemy import Engine, text


def up(engine: Engine) -> None:
    """Create llm_usage table."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS llm_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
                cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost REAL NOT NULL DEFAULT 0.0,
                created_at DATETIME NOT NULL DEFAULT (datetime('now'))
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_llm_usage_created_at ON llm_usage (created_at)"
        ))
