"""Policy comes from Notion now: registry table + import of the local rule files.

Two folders used to hold policy as files in this repo — ``company_rules/`` (tone, CS
rules, the no-price-in-the-first-reply rule) and ``knowledge_base/`` (plans, pricing,
FAQs). Both needed a deploy to change, and ``company_rules/`` is not even packaged into
the wheel (pyproject packages only ``src*``), so a wheel install silently drafted replies
with no rules at all.

This migration creates ``policy_sources`` — the operator's list of Notion pages plus the
last copy synced from each — and moves the two rule files into it as ``mode='rules'``
rows so nothing is lost the moment the folder is deleted. Rows imported from files have
no ``notion_page_id`` of their own, so they get a stable ``file:<name>`` key; pointing one
at Notion later is an edit on the 정책 문서 screen, not another migration.

``knowledge_base/*.md`` is NOT re-imported here: those documents were already loaded into
``knowledge_documents`` by a bundled importer, which was the source of
truth for them until their Notion pages are registered.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

# Under src/, so it ships in the wheel. The old company_rules/ folder sat at the repo
# root, which pyproject does not package — a wheel install drafted replies with no rules
# at all and nothing said so. Reading the seed from here also makes this migration
# self-contained: deleting the folder in the same commit cannot lose the text.
_RULES_DIR = Path(__file__).resolve().parents[1] / "seeds" / "policy"


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    is_sqlite = engine.dialect.name == "sqlite"
    ts_default = "CURRENT_TIMESTAMP" if is_sqlite else "NOW()"
    autoincrement = "INTEGER PRIMARY KEY AUTOINCREMENT" if is_sqlite else "SERIAL PRIMARY KEY"

    # **``order_index`` · ``status`` · ``summary`` · ``effective_on`` · ``edited_at`` 은
    # 0043~0101 을 살았던 칸입니다.** 새 DB 는 0001 의 ``create_all`` 이 **지금** 모델로
    # 표를 만들어 그 칸들이 없는 채로 시작하는데, 0043~0064 의 씨앗들은 그 이름으로
    # INSERT 합니다. 옛 마이그레이션의 SQL 을 고쳐 역사를 다시 쓰는 대신 그때의 모양을
    # 세워 두고, 0101 이 그때처럼 다시 지웁니다 (0019 가 channel/subject 에 하는 것과 같음).
    if "policy_sources" in set(inspector.get_table_names()):
        existing = {c["name"] for c in inspector.get_columns("policy_sources")}
        legacy = (
            ("order_index", "INTEGER NOT NULL DEFAULT 100"),
            ("status", "VARCHAR NOT NULL DEFAULT 'active'"),
            ("summary", "TEXT"),
            ("effective_on", "VARCHAR(10)"),
            ("edited_at", "TIMESTAMP"),
        )
        with engine.begin() as conn:
            for column, spec in legacy:
                if column not in existing:
                    conn.execute(text(f"ALTER TABLE policy_sources ADD COLUMN {column} {spec}"))

    if "policy_sources" not in set(inspector.get_table_names()):
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE policy_sources (
                        id {autoincrement},
                        label VARCHAR NOT NULL,
                        notion_url TEXT NOT NULL,
                        notion_page_id VARCHAR NOT NULL UNIQUE,
                        mode VARCHAR NOT NULL DEFAULT 'knowledge',
                        order_index INTEGER NOT NULL DEFAULT 100,
                        status VARCHAR NOT NULL DEFAULT 'active',
                        body TEXT,
                        title VARCHAR,
                        summary TEXT,
                        last_synced_at TIMESTAMP,
                        last_error TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT {ts_default},
                        updated_at TIMESTAMP NOT NULL DEFAULT {ts_default}
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_policy_sources_page "
                    "ON policy_sources (notion_page_id)"
                )
            )
        logger.info("0043: policy_sources created.")

    # Carry the two rule files in so deleting the folder loses nothing.
    if not _RULES_DIR.exists():
        logger.info("0043: no company_rules/ folder to import (already migrated).")
        return

    # The identity column was renamed to ``doc_key`` in 0050, and ``notion_url`` dropped
    # there. On a database that already ran this migration none of that matters — it is
    # recorded as applied and never runs again. It matters on a FRESH one: 0001 builds
    # every table from today's models, so the CREATE above is skipped and this INSERT
    # meets the post-0050 shape. An old migration has to survive the schema catching up
    # to it, or a new environment cannot be created at all (CI creates one every run).
    columns = {column["name"] for column in inspect(engine).get_columns("policy_sources")}
    key_column = "doc_key" if "doc_key" in columns else "notion_page_id"
    url_column = ", notion_url" if "notion_url" in columns else ""
    url_value = ", ''" if "notion_url" in columns else ""

    with engine.begin() as conn:
        existing = {
            row[0]
            for row in conn.execute(text(f"SELECT {key_column} FROM policy_sources")).fetchall()
        }
        for order, path in enumerate(sorted(_RULES_DIR.glob("rule_*.md")), start=1):
            key = f"file:{path.name}"
            if key in existing:
                continue
            conn.execute(
                text(
                    f"INSERT INTO policy_sources (label{url_column}, {key_column}, mode, "
                    "order_index, status, body, title, created_at, updated_at) "
                    f"VALUES (:label{url_value}, :key, 'rules', :order_index, 'active', :body, "
                    f":label, {ts_default}, {ts_default})"
                ),
                {
                    "label": path.stem,
                    "key": key,
                    "order_index": order * 10,
                    "body": path.read_text(encoding="utf-8").strip(),
                },
            )
            logger.info("0043: imported company rule %s", path.name)
