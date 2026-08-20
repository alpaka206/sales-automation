"""허브스팟에서 끌어온 기록에 티켓을 붙입니다 — 일회성 정리.

가져온 기록은 **연락처** 단위로 옵니다. 어느 티켓의 이야기인지는 허브스팟이 안 알려 줍니다.
그런데 「이 티켓의 기록」은 티켓 단위라, 붙이지 않으면 우리가 보낸 답이 고객 단위 목록에만
있고 정작 그 문의 화면에서는 **아무것도 안 보입니다** — 「문의 온 거는 있는데 우리가 보낸 건
없어」(2026-08-20 운영자).

규칙은 `conversation_history.conversation_for_touchpoint` 과 같습니다: **그 시점에 열려
있던 가장 최근 문의.** 대화가 하나뿐인 사람은(실측 99명 중 86명) 그냥 그것이고, 여럿이면
기록 시각 이전에 시작된 것 중 마지막입니다. 기록이 첫 문의보다 오래됐으면 첫 문의에
붙입니다 — 그 사람의 이야기는 거기서 시작하고, 아무 데도 안 붙는 것보다 낫습니다.

**틀릴 수 있습니다.** 한 사람이 여러 문의를 겹쳐서 진행하면 시간만으로는 못 가릅니다.
그래도 붙이는 쪽이 낫습니다: 안 붙이면 그 화면에는 **하나도** 안 보이고, 잘못 붙어도
고객 단위 목록에는 그대로 있어 읽는 사람이 알아볼 수 있습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

# ① 그 기록보다 먼저 시작된 문의 중 마지막.
_BY_TIME = """
UPDATE customer_interactions SET conversation_id = (
    SELECT c.id FROM conversations c
    WHERE c.contact_id = customer_interactions.contact_id
      AND c.created_at <= customer_interactions.happened_at
    ORDER BY c.created_at DESC, c.id DESC
    LIMIT 1
)
WHERE conversation_id IS NULL
"""

# ② 첫 문의보다도 오래된 기록은 그 첫 문의에.
_EARLIEST = """
UPDATE customer_interactions SET conversation_id = (
    SELECT c.id FROM conversations c
    WHERE c.contact_id = customer_interactions.contact_id
    ORDER BY c.created_at ASC, c.id ASC
    LIMIT 1
)
WHERE conversation_id IS NULL
"""


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    if not {"customer_interactions", "conversations"} <= tables:
        logger.info("0082: 표가 없습니다, 건너뜁니다")
        return
    with engine.begin() as conn:
        before = conn.execute(
            text("SELECT count(*) FROM customer_interactions WHERE conversation_id IS NULL")
        ).scalar_one()
        conn.execute(text(_BY_TIME))
        conn.execute(text(_EARLIEST))
        after = conn.execute(
            text("SELECT count(*) FROM customer_interactions WHERE conversation_id IS NULL")
        ).scalar_one()
    logger.info(
        "0082: 기록 %d건에 티켓을 붙였습니다. 붙일 문의가 없는 기록 %d건은 그대로 둡니다.",
        before - after, after,
    )
