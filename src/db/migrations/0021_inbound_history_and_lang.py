"""Inbound history, summaries, language tracking, and auto-ack template.

Adds (all additive / nullable so they're safe on populated tables):
- contacts.role_description        — operator-editable "what they do" note
- conversations.inquiry_language   — language every reply in the thread must use
- conversations.summary            — rolling LLM summary
- conversations.customer_requests  — rolling list of standing requests
- messages.target_language         — language a draft must be SENT in
- conversation_progress            — append-only dated processing log ("처리경과")

Seeds the editable ``auto_ack`` email template (Korean) used for the immediate
acknowledgement reply. Idempotent; works on SQLite and Postgres.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_AUTO_ACK_KO = (
    "안녕하세요 {name}님,\n\n"
    "문의 주셔서 감사합니다. 보내주신 메일은 잘 도착했으며, "
    "담당자가 내용을 확인한 뒤 24시간 이내에 답변드리겠습니다.\n\n"
    "조금만 기다려 주시면 빠르게 안내드리겠습니다.\n\n"
    "감사합니다."
)


def _add_columns(engine: Engine, table: str, columns: list[tuple[str, str]]) -> None:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    with engine.begin() as conn:
        for column, ddl in columns:
            if column in existing:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            logger.info("0021: added %s.%s", table, column)


def up(engine: Engine) -> None:
    insp = inspect(engine)
    is_sqlite = engine.dialect.name == "sqlite"
    ts_default = "CURRENT_TIMESTAMP" if is_sqlite else "now()"
    identity = "AUTOINCREMENT" if is_sqlite else "GENERATED ALWAYS AS IDENTITY"

    _add_columns(engine, "contacts", [("role_description", "TEXT")])
    _add_columns(
        engine,
        "conversations",
        [
            ("inquiry_language", "VARCHAR(8)"),
            ("summary", "TEXT"),
            ("customer_requests", "TEXT"),
        ],
    )
    _add_columns(engine, "messages", [("target_language", "VARCHAR(8)")])

    tables = set(insp.get_table_names())
    if "conversation_progress" not in tables:
        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE TABLE conversation_progress (
                    id INTEGER PRIMARY KEY {identity},
                    conversation_id INTEGER NOT NULL,
                    kind VARCHAR(32) NOT NULL DEFAULT 'note',
                    detail TEXT NOT NULL,
                    actor VARCHAR,
                    created_at TIMESTAMP NOT NULL DEFAULT {ts_default}
                )
                """))
            logger.info("0021: created conversation_progress table.")

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_conv_progress_conv "
                "ON conversation_progress (conversation_id)"
            )
        )

    # Seed the auto-ack template (skip if email_templates is absent or key exists).
    if "email_templates" in tables:
        with engine.begin() as conn:
            existing = {
                r[0]
                for r in conn.execute(
                    text("SELECT key FROM email_templates WHERE key = 'auto_ack'")
                )
            }
            if "auto_ack" not in existing:
                conn.execute(
                    text(
                        "INSERT INTO email_templates (key, name, language, channel, "
                        "body, description, status, version, created_at, updated_at) "
                        "VALUES ('auto_ack', :name, 'ko', 'email', :body, :desc, "
                        f"'active', 1, {ts_default}, {ts_default})"
                    ),
                    {
                        "name": "자동 접수확인 (한국어 원본)",
                        "body": _AUTO_ACK_KO,
                        "desc": (
                            "새 문의가 도착하면 즉시 자동 발송되는 접수확인 메일. "
                            "{name} 자리에 고객 이름이 들어가고, 문의 언어로 자동 번역되어 나갑니다."
                        ),
                    },
                )
                logger.info("0021: seeded auto_ack email template.")

    logger.info("0021: inbound history + language columns ready.")
