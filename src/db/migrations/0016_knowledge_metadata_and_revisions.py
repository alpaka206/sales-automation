"""Add metadata columns to knowledge_documents and create the revisions table.

New columns (router + provenance):
  - tags JSON, summary TEXT, author VARCHAR, status VARCHAR DEFAULT 'active',
    version INTEGER DEFAULT 1
New table knowledge_document_revisions: append-only edit history.

Idempotent and works on both SQLite and Postgres.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def _add_column(conn, table: str, column: str, ddl: str, existing: set[str]) -> None:
    if column in existing:
        return
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    logger.info("0016: added %s.%s", table, column)


def up(engine: Engine) -> None:
    insp = inspect(engine)
    is_sqlite = engine.dialect.name == "sqlite"
    ts_default = "CURRENT_TIMESTAMP" if is_sqlite else "now()"

    # --- knowledge_documents: new columns ---
    if "knowledge_documents" in insp.get_table_names():
        existing = {c["name"] for c in insp.get_columns("knowledge_documents")}
        with engine.begin() as conn:
            _add_column(conn, "knowledge_documents", "tags", "JSON", existing)
            _add_column(conn, "knowledge_documents", "summary", "TEXT", existing)
            _add_column(conn, "knowledge_documents", "author", "VARCHAR", existing)
            _add_column(
                conn,
                "knowledge_documents",
                "status",
                "VARCHAR NOT NULL DEFAULT 'active'",
                existing,
            )
            _add_column(
                conn, "knowledge_documents", "version", "INTEGER NOT NULL DEFAULT 1", existing
            )

    # --- knowledge_document_revisions: new table ---
    if "knowledge_document_revisions" not in insp.get_table_names():
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE knowledge_document_revisions (
                        id INTEGER PRIMARY KEY
                            {'AUTOINCREMENT' if is_sqlite else 'GENERATED ALWAYS AS IDENTITY'},
                        document_id INTEGER,
                        slug VARCHAR NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        title VARCHAR NOT NULL,
                        categories JSON,
                        tags JSON,
                        summary TEXT,
                        scope VARCHAR NOT NULL DEFAULT 'both',
                        body TEXT NOT NULL,
                        author VARCHAR,
                        status VARCHAR NOT NULL DEFAULT 'active',
                        change_note TEXT,
                        edited_by VARCHAR,
                        created_at TIMESTAMP NOT NULL DEFAULT {ts_default}
                    )
                    """
                )
            )
            logger.info("0016: created knowledge_document_revisions table.")

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_kdoc_rev_document_id "
                "ON knowledge_document_revisions (document_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_kdoc_rev_slug "
                "ON knowledge_document_revisions (slug)"
            )
        )
    logger.info("0016: knowledge metadata + revisions ready.")
