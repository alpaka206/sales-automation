"""정책 문서에 메일 제목 — ``policy_sources.subject``.

「기본 메일 템플릿 ENG」 같은 문서는 그 자체가 회신 한 통의 본보기인데, 제목을 적어 둘 곳이
없었습니다. 본문 안에 ``Subject: ...`` 로 적으면 모델이 그 줄을 **본문에 그대로 옮겨 적는**
일이 생깁니다 — 메일 첫 줄이 "Subject: ..." 인 메일이 나갑니다.

그래서 열로 둡니다. 그리고 **코드가** 꺼내 씁니다: 초안에 쓰인 문서 중 제목을 가진 것이
있으면 그 제목을 쓰고, 없으면 예전처럼 ``RE: <고객이 쓴 제목>`` 입니다. 모델에게 제목을
고르게 하지 않는 이유는 CODE GUARD 3 이 있는 이유와 같습니다 — 제목은 지어낼 수 있는
자리이고, 그러면 RE: 가 겹치거나 엉뚱한 언어로 나갑니다.

여러 문서가 제목을 가지고 있으면 **첫 번째**를 씁니다(라우터가 준 순서 = 제목 순).
케이스가 여럿인 문서(견적 4가지)는 지금처럼 본문 안에서 케이스별로 적어 두면 됩니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "policy_sources" not in set(inspector.get_table_names()):
        logger.info("0058: policy_sources missing; skipping.")
        return
    if "subject" in {column["name"] for column in inspector.get_columns("policy_sources")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE policy_sources ADD COLUMN subject TEXT"))
    logger.info("0058: policy_sources.subject added.")
