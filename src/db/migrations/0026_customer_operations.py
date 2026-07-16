"""Add minimal customer history, pipeline, contract, and payment records."""

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
    json_type = "JSON" if engine.dialect.name == "postgresql" else "TEXT"
    with engine.begin() as conn:
        if "customer_profiles" not in tables:
            conn.execute(text(f"""
                CREATE TABLE customer_profiles (
                    contact_id INTEGER PRIMARY KEY,
                    customer_state VARCHAR(32) NOT NULL DEFAULT 'negotiation',
                    pipeline_stage VARCHAR(32) NOT NULL DEFAULT 'new',
                    lead_temperature VARCHAR(16), next_action TEXT, next_action_at TIMESTAMP,
                    industry VARCHAR(128), user_seq VARCHAR(128), current_plan VARCHAR(64),
                    qualification VARCHAR(16), lost_reason TEXT, source VARCHAR(64), notes TEXT,
                    last_synced_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT {timestamp},
                    updated_at TIMESTAMP NOT NULL DEFAULT {timestamp}
                )
            """))
        if "customer_interactions" not in tables:
            conn.execute(text(f"""
                CREATE TABLE customer_interactions (
                    id {id_column},
                    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                    conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
                    channel VARCHAR(32) NOT NULL DEFAULT 'manual',
                    direction VARCHAR(16) NOT NULL DEFAULT 'note', subject VARCHAR(300),
                    summary TEXT NOT NULL, context TEXT, external_id VARCHAR(255),
                    artifact_url TEXT, happened_at TIMESTAMP NOT NULL DEFAULT {timestamp},
                    created_at TIMESTAMP NOT NULL DEFAULT {timestamp}
                )
            """))
        if "contract_records" not in tables:
            conn.execute(text(f"""
                CREATE TABLE contract_records (
                    id {id_column},
                    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                    status VARCHAR(32) NOT NULL DEFAULT 'draft', plan VARCHAR(64),
                    amount FLOAT, currency VARCHAR(8) NOT NULL DEFAULT 'KRW',
                    payment_method VARCHAR(32), contract_date TIMESTAMP,
                    payment_due_at TIMESTAMP, paid_at TIMESTAMP, expires_at TIMESTAMP,
                    language_pairs {json_type}, unit_price VARCHAR(128), quote_url TEXT,
                    invoice_url TEXT, payment_url TEXT, notes TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT {timestamp},
                    updated_at TIMESTAMP NOT NULL DEFAULT {timestamp}
                )
            """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_customer_interactions_contact ON customer_interactions (contact_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_customer_interactions_external ON customer_interactions (external_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_contract_records_contact ON contract_records (contact_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_contract_records_expires ON contract_records (expires_at)"))
