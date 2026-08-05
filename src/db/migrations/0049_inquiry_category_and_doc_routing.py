"""문의 유형을 저장하고, 어떤 문서를 볼지를 문서 자신이 들고 있게 합니다.

세 가지가 한 변경입니다 — 같은 질문("이 문의에 무엇으로 답하나")의 세 조각이라서:

``conversations.inquiry_category``
    분류기가 내던 유형을 다시 저장합니다. 0041 이 이 열을 없앤 이유는 ``topic`` 하나에
    유형과 제목이 섞여 있었기 때문이지 유형이 쓸모없어서가 아니었습니다. 목록에서
    채널(전부 "email" 이라 아무것도 구분하지 못하는 값) 대신 보여줄 것이 이것이고,
    실제로 어떤 문의가 오는지도 이 열이 없으면 셀 수 없습니다.

``policy_sources.edited_at``
    콘솔에서 본문을 고칠 수 있게 되었으므로, 그 뒤 같은 문서를 업로드하면 파일 내용으로
    되돌아갑니다. 문제는 덮어쓰는 것이 아니라 **조용히** 덮어쓰는 것이라, 화면이 "콘솔에서
    수정함" 이라고 말할 수 있도록 시각을 남깁니다.

``messages.review_note`` 는 지웁니다 (0047 에서 추가). 어차피 모든 상세 회신이 사람 승인을
기다리므로 "검토 필요" 는 줄 세우기를 도와주는 표시였는데, 유형이 목록에 보이면 그 일을
유형이 더 정확하게 합니다 — CS 문의인지 스팸인지가 "확인이 필요합니다" 라는 문장보다
많은 것을 말합니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        if "conversations" in tables:
            if "inquiry_category" not in _columns(inspector, "conversations"):
                conn.execute(
                    text("ALTER TABLE conversations ADD COLUMN inquiry_category VARCHAR(32)")
                )

        if "policy_sources" in tables:
            if "edited_at" not in _columns(inspector, "policy_sources"):
                conn.execute(text("ALTER TABLE policy_sources ADD COLUMN edited_at TIMESTAMP"))

        # 0047 이 추가한 열. SQLite 3.35+ 와 Postgres 모두 DROP COLUMN 을 지원합니다.
        if "messages" in tables and "review_note" in _columns(inspector, "messages"):
            try:
                conn.execute(text("ALTER TABLE messages DROP COLUMN review_note"))
            except Exception:
                # 아주 오래된 SQLite 라면 열이 남습니다 — 읽는 곳이 없으므로 무해합니다.
                logger.warning("0049: could not drop messages.review_note; leaving it unused.")

    logger.info("0049: inquiry_category, document routing columns, review_note dropped.")
