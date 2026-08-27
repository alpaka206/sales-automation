"""판본은 한 표에 모입니다 — 그리고 정책 문서에도 이력이 생깁니다 (2026-08-27 운영자 지시).

전에는 이랬습니다:

- ``email_template_revisions`` — 생성·수정·삭제·되돌리기마다 쌓였는데 **읽는 라우트도
  화면도 없었습니다.** 마이그레이션 0069 는 주석에 "이력 화면에 예전 본문이 그대로
  남습니다" 라고 적어 두었지만, 그 화면은 존재한 적이 없습니다.
- 정책 문서 — 이력이 **아예 없었습니다.** 그 몫이라던 ``knowledge_document_revisions`` 는
  0016 이 만들고 아무도 쓰지 않았고 0095 가 지웠습니다.

이제 ``document_revisions`` 하나가 둘 다 받습니다. 표를 종류마다 두면 읽는 화면도 라우트도
둘이 되고, 둘 중 하나에만 이력이 달리는 날이 옵니다 — 방금 그랬습니다.

**옛 행은 그대로 옮깁니다.** 0069·0086 이 운영자가 쓴 서식을 고치기 전에 남긴 스냅샷이
거기 있고, 그것이 「마이그레이션이 내 글을 어떻게 바꿨나」의 유일한 사본입니다. 옛 표에는
``version`` 이 없어서 템플릿별로 시간순 번호를 매깁니다 — 실제 판 번호와 어긋날 수 있지만,
목록을 정렬하고 「몇 번째 판인가」를 말하는 데는 그것으로 충분합니다.

``policy_sources.version`` 도 여기서 생깁니다. ``email_templates`` 는 이미 갖고 있습니다.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_OLD = "email_template_revisions"
_NEW = "document_revisions"


def _create(conn, dialect: str) -> None:
    serial = "SERIAL PRIMARY KEY" if dialect != "sqlite" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ts = "TIMESTAMP" if dialect != "sqlite" else "DATETIME"
    default_now = "CURRENT_TIMESTAMP"
    conn.execute(
        text(
            f"""
            CREATE TABLE {_NEW} (
                id {serial},
                kind VARCHAR(32) NOT NULL,
                document_id INTEGER,
                doc_key VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                body TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                status VARCHAR NOT NULL DEFAULT 'active',
                change_note TEXT,
                edited_by VARCHAR,
                extra {"JSON" if dialect != "sqlite" else "TEXT"},
                created_at {ts} NOT NULL DEFAULT {default_now}
            )
            """
        )
    )
    for column in ("kind", "document_id", "doc_key"):
        conn.execute(
            text(f"CREATE INDEX ix_{_NEW}_{column} ON {_NEW} ({column})")
        )
    logger.info("0096: created %s.", _NEW)


def _carry_over(conn, dialect: str) -> int:
    """옛 이력을 새 표로. 템플릿별 시간순으로 판 번호를 매깁니다."""
    rows = conn.execute(
        text(
            f"SELECT id, template_id, key, name, language, channel, body, description, "
            f"status, change_note, edited_by, created_at FROM {_OLD} "
            f"ORDER BY template_id, created_at, id"
        )
    ).fetchall()
    seen: dict[object, int] = {}
    moved = 0
    for row in rows:
        seen[row.template_id] = seen.get(row.template_id, 0) + 1
        extra = {
            "language": row.language,
            "channel": row.channel,
            "description": row.description,
        }
        payload = json.dumps({k: v for k, v in extra.items() if v is not None}, ensure_ascii=False)
        # Postgres 의 JSON 열에는 문자열이 그대로 안 들어갑니다. SQLite 의 JSON 은 TEXT 라
        # 캐스트를 붙이면 오히려 깨집니다.
        value = "CAST(:extra AS JSON)" if dialect != "sqlite" else ":extra"
        conn.execute(
            text(
                f"INSERT INTO {_NEW} (kind, document_id, doc_key, title, body, version, "
                f"status, change_note, edited_by, extra, created_at) VALUES "
                f"('email_template', :doc_id, :key, :title, :body, :version, :status, "
                f":note, :by, {value}, :at)"
            ),
            {
                "doc_id": row.template_id,
                "key": row.key,
                "title": row.name,
                "body": row.body or "",
                "version": seen[row.template_id],
                "status": row.status or "active",
                "note": row.change_note,
                "by": row.edited_by,
                "extra": payload,
                "at": row.created_at,
            },
        )
        moved += 1
    return moved


def up(engine: Engine) -> None:
    dialect = engine.dialect.name
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        if _NEW not in tables:
            _create(conn, dialect)

        if _OLD in tables:
            moved = _carry_over(conn, dialect)
            conn.execute(text(f"DROP TABLE {_OLD}"))
            logger.info("0096: carried %d revision(s) over and dropped %s.", moved, _OLD)

        if "policy_sources" in tables:
            columns = {c["name"] for c in inspector.get_columns("policy_sources")}
            if "version" not in columns:
                conn.execute(
                    text("ALTER TABLE policy_sources ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
                )
                logger.info("0096: policy_sources.version added.")
