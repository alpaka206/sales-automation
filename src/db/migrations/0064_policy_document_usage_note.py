"""정책 문서에 "언제 쓰는가" 칸 — 라우터가 보는 것을 사람이 적습니다.

문서를 고르는 것은 코드가 아니라 모델입니다(그게 옳습니다 — 문서는 이름이 바뀌고 지워지고
새로 생기는데, 매핑을 코드에 굳히면 그때마다 아무 흔적 없이 끊깁니다). 그런데 모델이 고를 때
보는 것은 본문이 아니라 인덱스 한 줄입니다:

    slug · title · categories · tags · summary

그리고 그 ``summary`` 는 **붙여넣은 본문의 첫 400자를 잘라 낸 것**이었습니다. 그래서 라우팅을
고치려면 본문 맨 위에 "이 문서는 ~일 때 씁니다" 를 적어 넣어야 했고, 용도와 내용이 한 덩어리로
섞였습니다 — 노션에서 다시 붙여넣으면 그 줄이 날아가는 것은 덤입니다.

칸을 따로 둡니다. 채우면 그게 요약이 되고, 비우면 예전처럼 본문 앞부분입니다. 열은
``policy_sources`` 에만 둡니다 — ``knowledge_documents.summary`` 가 이미 라우터가 읽는 자리라,
사본 쪽에 열을 하나 더 만들 이유가 없습니다.

같이 고치는 것: 사본의 ``categories``. 콘솔로 들어온 문서는 전부 ``["policy"]`` 로 저장돼
있었는데, 그 값은 문의 유형(sales/support/spam…) 어디와도 맞지 않습니다. 평소에는 모델이
골라 주니 티가 안 나지만, **라우터가 실패해 유형 매칭으로 떨어지는 순간 후보가 0개**가 되어
문서 없이 답을 씁니다. ``["all"]`` 로 바꿉니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "policy_sources" in tables:
        if "usage_note" not in {c["name"] for c in inspector.get_columns("policy_sources")}:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE policy_sources ADD COLUMN usage_note TEXT"))
            logger.info("0064: policy_sources.usage_note added.")

    if "knowledge_documents" in tables:
        # ORM 로 씁니다. ``categories`` 는 JSON 열이고, 리스트를 문자열로 직렬화하는 방식이
        # SQLite 와 PostgreSQL 에서 다릅니다 — 원시 SQL 로는 한쪽에서 반드시 틀립니다.
        from sqlalchemy.orm import Session

        from ..models import KnowledgeDocument

        fixed = 0
        with Session(engine) as session:
            for doc in session.query(KnowledgeDocument).all():
                if "policy" in (doc.categories or []):
                    doc.categories = ["all"]
                    fixed += 1
            session.commit()
        logger.info("0064: %d knowledge document(s) moved from 'policy' to 'all'.", fixed)
