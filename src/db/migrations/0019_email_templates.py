"""Create email_templates and email_template_revisions tables + seed defaults.

email_templates: editable email building-block snippets (signature, greeting,
footer, ...) keyed by ``key`` and optionally scoped per ``language``.
email_template_revisions: append-only edit history (no hard FK so revisions
outlive their source template).

Idempotent and works on both SQLite and Postgres.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_SEED = [
    {
        "key": "greeting",
        "name": "인사말",
        "language": "all",
        "body": "안녕하세요, 문의 주셔서 감사합니다.",
    },
    {
        "key": "signature_ko",
        "name": "서명 (한국어)",
        "language": "ko",
        "body": "감사합니다.\nPERSO AI 드림\nhttps://perso.ai",
    },
    {
        "key": "signature_en",
        "name": "Signature (English)",
        "language": "en",
        "body": "Best regards,\nThe PERSO AI Team\nhttps://perso.ai",
    },
    {
        "key": "footer_note",
        "name": "푸터 안내문",
        "language": "all",
        "body": "",
    },
]


def up(engine: Engine) -> None:
    insp = inspect(engine)
    is_sqlite = engine.dialect.name == "sqlite"
    ts_default = "CURRENT_TIMESTAMP" if is_sqlite else "now()"
    identity = "AUTOINCREMENT" if is_sqlite else "GENERATED ALWAYS AS IDENTITY"

    tables = set(insp.get_table_names())

    # **``channel`` 과 ``subject`` 는 0019 부터 0100 까지 살았던 칸입니다.**
    # 새 DB 는 0001 의 ``create_all`` 이 **지금** 모델로 표를 만들므로 그 둘이 없는 채로
    # 시작하는데, 0019~0056 의 씨앗들은 그 이름으로 INSERT 합니다. 여덟 개 넘는 옛
    # 마이그레이션의 SQL 을 고쳐 역사를 다시 쓰는 대신, 여기서 **그때의 모양을 만들어
    # 둡니다** — 0100 이 그때처럼 다시 지웁니다. 이미 적용된 DB 에서는 아무 일도 안
    # 일어납니다(칸이 있거나, 0100 이 이미 지웠거나).
    if "email_templates" in tables:
        existing = {c["name"] for c in insp.get_columns("email_templates")}
        with engine.begin() as conn:
            if "channel" not in existing:
                conn.execute(text(
                    "ALTER TABLE email_templates ADD COLUMN channel VARCHAR "
                    "NOT NULL DEFAULT 'email'"
                ))
            if "subject" not in existing:
                conn.execute(text("ALTER TABLE email_templates ADD COLUMN subject TEXT"))

    if "email_templates" not in tables:
        with engine.begin() as conn:
            conn.execute(text(f"""
                    CREATE TABLE email_templates (
                        id INTEGER PRIMARY KEY {identity},
                        key VARCHAR NOT NULL UNIQUE,
                        name VARCHAR NOT NULL,
                        language VARCHAR NOT NULL DEFAULT 'all',
                        channel VARCHAR NOT NULL DEFAULT 'email',
                        body TEXT NOT NULL,
                        description TEXT,
                        status VARCHAR NOT NULL DEFAULT 'active',
                        version INTEGER NOT NULL DEFAULT 1,
                        author VARCHAR,
                        created_at TIMESTAMP NOT NULL DEFAULT {ts_default},
                        updated_at TIMESTAMP NOT NULL DEFAULT {ts_default}
                    )
                    """))
            logger.info("0019: created email_templates table.")

    if "email_template_revisions" not in tables:
        with engine.begin() as conn:
            conn.execute(text(f"""
                    CREATE TABLE email_template_revisions (
                        id INTEGER PRIMARY KEY {identity},
                        template_id INTEGER,
                        key VARCHAR NOT NULL,
                        name VARCHAR NOT NULL,
                        language VARCHAR NOT NULL DEFAULT 'all',
                        channel VARCHAR NOT NULL DEFAULT 'email',
                        body TEXT NOT NULL,
                        description TEXT,
                        status VARCHAR NOT NULL DEFAULT 'active',
                        change_note TEXT,
                        edited_by VARCHAR,
                        created_at TIMESTAMP NOT NULL DEFAULT {ts_default}
                    )
                    """))
            logger.info("0019: created email_template_revisions table.")

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_email_tpl_rev_template_id "
                "ON email_template_revisions (template_id)"
            )
        )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_email_templates_key " "ON email_templates (key)")
        )

    # --- seed defaults (skip any key that already exists) ---
    with engine.begin() as conn:
        existing = {r[0] for r in conn.execute(text("SELECT key FROM email_templates"))}
        for row in _SEED:
            if row["key"] in existing:
                continue
            conn.execute(
                text(
                    "INSERT INTO email_templates (key, name, language, channel, "
                    "body, status, version, created_at, updated_at) "
                    "VALUES (:key, :name, :language, 'email', :body, 'active', 1, "
                    f"{ts_default}, {ts_default})"
                ),
                row,
            )
            logger.info("0019: seeded email template %s", row["key"])

    logger.info("0019: email templates ready.")
