"""``policy_sources.effective_on`` — 이 문서가 **언제 기준인가**.

저장한 시각(``edited_at``)과 다른 사실입니다. 「크레딧 차감 정책 (26.04.28 기준)」을 오늘
붙여넣으면 ``edited_at`` 은 오늘이고, 목록은 "어제 손댄 최신 문서"처럼 보입니다. 실제로는
넉 달 된 정책이고, 그 차이가 "이 숫자 아직 맞나?" 를 물어볼지 말지를 가릅니다.

그래서 기준일은 **적으면 그 값, 안 적으면 저장 시각**입니다. 안 적었을 때 빈칸으로 두면
날짜가 아예 없는 문서가 생기고, 목록에서 무엇이 오래됐는지 볼 수 없게 됩니다.

문자열로 둡니다 — 화면의 ``<input type="date">`` 가 ``YYYY-MM-DD`` 로만 보내므로 파싱할
것이 없고, 그대로 보여주면 됩니다. 프롬프트에는 들어가지 않습니다: 문서가 언제 기준인지는
운영자가 판단할 일이지 모델이 회신에 쓸 내용이 아닙니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "policy_sources" not in set(inspector.get_table_names()):
        logger.info("0052: policy_sources missing; skipping.")
        return
    columns = {column["name"] for column in inspector.get_columns("policy_sources")}
    if "effective_on" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE policy_sources ADD COLUMN effective_on VARCHAR(10)"))
    logger.info("0052: policy_sources.effective_on added.")
