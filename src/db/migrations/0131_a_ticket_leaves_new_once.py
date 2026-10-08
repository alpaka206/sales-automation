"""``conversations.left_new_at`` — 이 문의가 처음 New 를 떠난 때 (2026-10-08).

콘솔 밖(허브스팟 받은편지함 · 개인 메일함)에서 첫 답을 보낸 New 티켓은 이제 저절로 Contacted(고객이 그 뒤에
썼으면 협의 중)로 간다(`ticket_history.advance_if_customer_replied`). 그 판정은 10분 폴러도 다시 돌리므로, 사람이
그 티켓을 New 로 되돌리면 10분 안에 또 옮기고 허브스팟과 워크북까지 다시 쓴다 — 사람과 싸운다. 그래서 자동 이동은
**New 를 떠난 적이 없는 문의에만** 돈다. 적는 곳은 `models._stage_left_new` 하나다(단계를 쓰는 길이 여럿이라).

**이미 New 가 아닌 문의는 지금 시각으로 채운다** — 그 문의는 New 를 떠난 적이 있고, 비워 두면 나중에 사람이 New 로
되돌렸을 때 자동 이동이 한 번 싸운다. 지금 New 인 문의는 비워 둔다(10-06 운영 사본 0건, 그 뒤 들어온 것이 몇 건).
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "conversations" not in set(inspector.get_table_names()):
        return
    if "left_new_at" in {c["name"] for c in inspector.get_columns("conversations")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE conversations ADD COLUMN left_new_at TIMESTAMP"))
        filled = conn.execute(text(
            "UPDATE conversations SET left_new_at = CURRENT_TIMESTAMP WHERE stage NOT IN ('new', 'initial')"
        )).rowcount
    logger.info("0131: conversations.left_new_at 를 만들고 New 가 아닌 문의 %s건을 채웠습니다.", filled)
