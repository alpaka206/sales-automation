"""후속 리마인더가 닫은 티켓 — 「되살렸다」와 「사람이 건드렸다」 두 칸 (2026-09-30).

- ``conversations.followup_revived_at`` — 시퀀스가 닫은 티켓을 고객 답장으로 협의 중에 되살린 때.
- ``conversations.followup_released_at`` — 시퀀스가 닫은 **뒤에** 누구든 단계를 옮긴 때
  (`models._stage_moved_after_followup_close` 가 적는다 — 단계를 쓰는 곳이 여럿이라 한 자리에서).

**왜 생겼나.** 시퀀스가 닫은 티켓(`followup_closed_at`)은 고객이 연락하면 Negotiating 으로 되살아난다. 그 뒤
사람이 그 티켓을 다시 Concluded 로 옮기면, 10분 스윕이 「시퀀스가 닫은 Concluded + 닫은 뒤의 고객 연락」을
**또** 보고 협의 중으로 되돌렸다 — 새 연락이 없어도, 사람이 옮길 때마다, 허브스팟과 워크북까지(2026-09-30
검증 워크플로가 네 방향에서 따로 재현). 닫는 단계를 Closed Lost 에서 Concluded 로 옮기면서 그 구멍이 사람이
문의를 끝낼 때 가장 흔히 쓰는 단계로 왔다. 스윕보다 먼저 사람이 손으로 협의 중에 옮겼다가 나중에 끝내도
같은 일이 난다(서비스가 자는 동안이 그렇다).

그래서 「시퀀스가 닫은 티켓」은 **닫은 뒤로 아무도 단계를 안 건드린 티켓**이다(`followup_released_at` 이 없거나
닫은 때보다 이르다). 빨간 표시(되살아난 티켓)는 시퀀스가 되살린 것만이다(`followup_revived_at`) — 사람이 손으로
협의 중에 옮긴 것은 되살아난 것이 아니다.

**값은 안 옮긴다.** 이 날까지 시퀀스가 닫은 티켓은 0건이다(운영 로그 — 첫 리마인더가 09-28).
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_COLUMNS = ("followup_revived_at", "followup_released_at")


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "conversations" not in set(inspector.get_table_names()):
        return
    have = {c["name"] for c in inspector.get_columns("conversations")}
    missing = [name for name in _COLUMNS if name not in have]
    if not missing:
        return
    with engine.begin() as conn:
        for name in missing:
            conn.execute(text(f"ALTER TABLE conversations ADD COLUMN {name} TIMESTAMP"))
    logger.info("0129: conversations.%s 를 만들었습니다.", " · ".join(missing))
