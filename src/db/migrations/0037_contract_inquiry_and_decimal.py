"""Link contracts to inquiries and store money as exact decimals."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def up(engine: Engine) -> None:
    if "contract_records" not in set(inspect(engine).get_table_names()):
        return
    columns = {item["name"] for item in inspect(engine).get_columns("contract_records")}
    with engine.begin() as conn:
        if "conversation_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE contract_records ADD COLUMN conversation_id INTEGER "
                    "REFERENCES conversations(id) ON DELETE SET NULL"
                )
            )
        if "sheet_client_id" not in columns:
            conn.execute(text("ALTER TABLE contract_records ADD COLUMN sheet_client_id INTEGER"))

        # A legacy contract is safe to link automatically only when the contact
        # has exactly one inquiry. Multi-inquiry rows remain explicitly unlinked.
        conn.execute(
            text(
                "UPDATE contract_records SET conversation_id = ("
                "SELECT MIN(c.id) FROM conversations c "
                "WHERE c.contact_id = contract_records.contact_id"
                ") WHERE conversation_id IS NULL AND 1 = ("
                "SELECT COUNT(*) FROM conversations c "
                "WHERE c.contact_id = contract_records.contact_id)"
            )
        )
        conn.execute(
            text(
                "UPDATE contract_records SET sheet_client_id = ("
                "SELECT c.sheet_client_id FROM conversations c "
                "WHERE c.id = contract_records.conversation_id"
                ") WHERE sheet_client_id IS NULL AND conversation_id IS NOT NULL"
            )
        )

        if engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    "ALTER TABLE contract_records ALTER COLUMN amount "
                    "TYPE NUMERIC(18, 2) USING amount::numeric(18, 2)"
                )
            )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_contract_records_conversation_id "
                "ON contract_records (conversation_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_contract_records_sheet_client_id "
                "ON contract_records (sheet_client_id)"
            )
        )
