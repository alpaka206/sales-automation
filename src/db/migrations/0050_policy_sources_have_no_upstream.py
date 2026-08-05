"""정책 문서에는 이제 원본이 없습니다 — 이 콘솔이 원본입니다.

노션에서 자동으로 받아 오는 경로를 전부 지웠습니다(공식 API 토큰 발급 불가, 쿠키 경로 403,
Export zip 은 부모 페이지 한 장만 실어 옴 — docs/정책문서-동기화-설계.md). 사람이 본문을
붙여넣는 것이 유일한 입구이므로, "언제 어디서 받아왔는가" 를 적어 두던 열들이 전부 답이 없는
질문이 되었습니다:

    notion_url        받아올 곳이 없습니다. 남겨 두면 "여기서 가져오는구나" 로 읽힙니다.
    last_synced_at    동기화가 없습니다. 화면은 마지막으로 손댄 시각(edited_at)을 씁니다.
    last_error        실패할 읽기가 없습니다.

``notion_page_id`` 는 이름만 틀린 것이 아니라 지금 담고 있는 값도 노션 페이지 id 가 아닙니다 —
제목에서 만든 해시이거나 예전 파일 시드의 ``file:01_tone.md`` 입니다. 등록부의 신원이라는
역할은 그대로 필요하므로 ``doc_key`` 로 이름만 바꿉니다. 지우고 새로 만들면 기존 행이 지식
문서 사본과 이어지지 못합니다.

되돌릴 때: 통합 토큰이 생겨 ``notion.fetch_page`` 를 되살린다면 이 열들을 다시 추가하면
됩니다. 그때 무엇을 확인해야 하는지는 설계 문서 §5 에 있습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_DROP = ("notion_url", "last_synced_at", "last_error")


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "policy_sources" not in set(inspector.get_table_names()):
        logger.info("0050: policy_sources missing; skipping.")
        return
    columns = {column["name"] for column in inspector.get_columns("policy_sources")}

    with engine.begin() as conn:
        if "notion_page_id" in columns and "doc_key" not in columns:
            conn.execute(
                text("ALTER TABLE policy_sources RENAME COLUMN notion_page_id TO doc_key")
            )
        for column in _DROP:
            if column not in columns:
                continue
            try:
                conn.execute(text(f"ALTER TABLE policy_sources DROP COLUMN {column}"))
            except Exception:
                # 아주 오래된 SQLite. 읽는 곳이 없으므로 남아 있어도 무해합니다.
                logger.warning("0050: could not drop policy_sources.%s; leaving it.", column)

    logger.info("0050: policy_sources now stands on its own (no upstream).")
