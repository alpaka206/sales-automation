"""Track durable 수주 DB synchronization for contracts."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def up(engine: Engine) -> None:
    if "contract_records" not in set(inspect(engine).get_table_names()):
        return
    columns = {item["name"] for item in inspect(engine).get_columns("contract_records")}
    with engine.begin() as conn:
        if "sheet_order_row" not in columns:
            conn.execute(text("ALTER TABLE contract_records ADD COLUMN sheet_order_row INTEGER"))
        if "sheet_synced_at" not in columns:
            conn.execute(text("ALTER TABLE contract_records ADD COLUMN sheet_synced_at TIMESTAMP"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_contract_records_sheet_synced_at "
                "ON contract_records (sheet_synced_at)"
            )
        )
