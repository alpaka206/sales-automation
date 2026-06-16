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
