"""Create knowledge_documents table for DB-backed knowledge base."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def up(engine: Engine) -> None:
    """Create the knowledge_documents table if it does not exist."""
    insp = inspect(engine)
    if "knowledge_documents" in insp.get_table_names():
        return

    is_sqlite = engine.dialect.name == "sqlite"
    ts_default = "CURRENT_TIMESTAMP" if is_sqlite else "now()"

    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE knowledge_documents (
                id INTEGER PRIMARY KEY {'AUTOINCREMENT' if is_sqlite else 'GENERATED ALWAYS AS IDENTITY'},
                title VARCHAR NOT NULL,
                slug VARCHAR NOT NULL UNIQUE,
                categories JSON,
                scope VARCHAR NOT NULL DEFAULT 'both',
                body TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT {ts_default},
                updated_at TIMESTAMP NOT NULL DEFAULT {ts_default}
            )
        """))
