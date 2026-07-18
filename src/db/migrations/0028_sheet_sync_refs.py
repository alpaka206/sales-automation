"""Track exact Google Sheet rows and extra order-sheet fields."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def _add(engine: Engine, table: str, column: str, sql_type: str) -> None:
    if table not in set(inspect(engine).get_table_names()):
        return
    columns = {item["name"] for item in inspect(engine).get_columns(table)}
    if column not in columns:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def up(engine: Engine) -> None:
    _add(engine, "contacts", "sheet_client_id", "INTEGER")
    _add(engine, "conversations", "sheet_inbound_row", "INTEGER")
    _add(
        engine,
        "contract_records",
        "sheet_fields",
        "JSON" if engine.dialect.name == "postgresql" else "TEXT",
    )
    with engine.begin() as conn:
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_contacts_sheet_client_id ON contacts (sheet_client_id)")
        )
