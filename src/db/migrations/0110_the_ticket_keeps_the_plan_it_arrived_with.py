"""``conversations.plan_snapshot`` — 문의가 들어온 시점의 플랜 (2026-09-07 운영자 지시).

티켓 상세의 「플랜 정보」와 MQL/PQL 은 `customer_profiles` 를 읽습니다. 리드 히스토리도
**같은 행**을 읽습니다. 그래서 고객이 나중에 플랜을 올리면 **몇 달 전 문의 화면의 값까지
같이 바뀌었습니다** — 그 문의를 판단하려고 여는 화면인데, 그때 이 사람이 무엇을 쓰고
있었는지가 남지 않았습니다.

이제 티켓은 자기 값을 들고, 리드 히스토리는 지금처럼 최신을 봅니다. **둘은 서로에게
번지지 않습니다**(운영자 지시): 한쪽을 고쳐도 다른 쪽은 그대로입니다.

칸을 다섯 개로 쪼개지 않고 JSON 하나로 둡니다 — 이 값은 `hubspot_record.RECORD_FIELDS`
목록을 그대로 베낀 사본이라, 그 목록이 바뀌면 열도 같이 바뀌어야 합니다. 조회 조건으로
쓰는 값도 아닙니다(화면이 그리기만 합니다).

**NULL 은 「그때 값을 모른다」입니다.** 이 칸이 생기기 전의 티켓 300여 건이 그렇고, 그
값은 어디에도 안 남아 있어 만들어 낼 수 없습니다. 그 티켓은 예전처럼 현재 값을 보여
줍니다 — 화면이 어느 쪽인지 적습니다. 운영자가 그 카드를 한 번 고치면 그때부터 자기 값을
갖습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "conversations" not in set(inspector.get_table_names()):
        logger.info("0110: conversations 없음, 건너뜁니다.")
        return
    if "plan_snapshot" in {c["name"] for c in inspector.get_columns("conversations")}:
        return
    # SQLite 에는 JSON 타입이 없고 TEXT 로 삽니다. Postgres 에서는 JSON 이 맞는 타입입니다 —
    # SQLAlchemy 의 JSON 이 양쪽을 같은 파이썬 dict 로 돌려줍니다.
    json_type = "JSON" if engine.dialect.name != "sqlite" else "TEXT"
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE conversations ADD COLUMN plan_snapshot {json_type}"))
    logger.info("0110: conversations.plan_snapshot 을 더했습니다.")
