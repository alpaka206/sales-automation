"""지운 것은 일주일 남습니다 — ``email_templates`` · ``policy_sources`` 에 ``deleted_at``.

운영자가 「항상 적용」 정책 문서 하나를 실수로 지웠고, 되돌릴 방법이 없었습니다. 그 종류는
DB 어디에도 사본이 없습니다(``mode='rules'`` 는 등록부에서 직접 읽힙니다). 저장소의 씨앗
파일에서 **원본**을 다시 넣는 것이 최선이었고, 그 사이 콘솔에서 고친 내용은 사라졌습니다.

이제 삭제는 행을 지우는 대신 ``status='deleted'`` + ``deleted_at`` 입니다. 읽는 쪽은 전부
이미 ``status='active'`` 만 보므로(서명 고르개 · 접수확인 조회 · ``_rules_from_db``) 거를
곳을 새로 만들지 않았고, 지운 즉시 발송·초안에서 빠지는 것도 그대로입니다. 달라지는 것은
목록뿐입니다: 흐리게 일주일 남았다가 청소됩니다. src/db/soft_delete.py
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table in ("email_templates", "policy_sources"):
        if table not in tables:
            continue
        if "deleted_at" in {c["name"] for c in inspector.get_columns(table)}:
            continue
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN deleted_at TIMESTAMP"))
        logger.info("0070: %s.deleted_at added.", table)
