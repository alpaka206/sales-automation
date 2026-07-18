"""Store encrypted user OAuth grants for external integrations."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def up(engine: Engine) -> None:
    if "integration_credentials" in set(inspect(engine).get_table_names()):
        return
    timestamp = "CURRENT_TIMESTAMP" if engine.dialect.name == "sqlite" else "now()"
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE integration_credentials (
                    provider VARCHAR(64) PRIMARY KEY,
                    account_email VARCHAR(320),
                    encrypted_payload TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT {timestamp},
                    updated_at TIMESTAMP NOT NULL DEFAULT {timestamp}
                )
                """
            )
        )
