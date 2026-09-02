"""티켓마다 **모든** 대화를 들고 있게 합니다 (2026-09-02 운영자 지시).

지금까지 이 콘솔은 티켓의 대화 대부분을 **가진 적이 없습니다.** 빼먹은 것이 아니라
가져오는 코드가 없었습니다 — `messages` 를 쓰는 곳이 셋뿐이고(첫 문의 하나 · 워크북
임포트 · 운영자가 쓴 초안) 그중 어느 것도 HubSpot 스레드를 읽지 않았습니다.

실측(2026-09-02): 티켓 **327건 중 133건**에서 고객 메시지 **627건**이 빠져 있었고, 채팅
채널 메시지 **896건**은 기존 경로로는 볼 수조차 없었습니다.

이 이관은 그 수집기(`agents/ticket_history`)가 설 자리를 만듭니다:

``conversations.history_synced_at``
    그 티켓의 대화를 마지막으로 받아온 시각. **NULL 이 「아직 한 번도 안 받았다」**이고,
    수집기는 NULL 먼저 · 그 다음 오래된 순으로 돕니다. 그래서 이 칸 하나가 진행 상황이자
    이어하기 지점입니다 — 배포가 나가도 다음 회차가 그 자리에서 계속하고, 한 바퀴를 다
    돌면 가장 오래된 것부터 다시 돌아 **새로 쌓인 대화도 저절로 들어옵니다.**
    (`customer_profiles.last_synced_at` 이 이미 쓰는 방식과 같습니다.)

``customer_interactions.external_id`` 에 **유니크**
    수집기는 HubSpot 메시지 id 를 `hubspot:conv:<id>` 로 적습니다. 유니크가 걸려 있어야
    **몇 번을 다시 돌려도 중복이 안 생깁니다** — 「지우고 새로 받기」가 필요 없어지고,
    실패한 회차를 그냥 다시 돌리면 됩니다.

    옛 행에는 NULL 이 많습니다(사람이 손으로 적은 기록). NULL 은 유니크 제약에 안 걸리므로
    그대로 둡니다. 값이 있는 행 중 중복이 있으면 인덱스를 못 만드는데, 그때는 조용히
    넘어갑니다 — 수집기는 넣기 전에 이미 있는지 보고 넣으므로(`_store`) 인덱스가 없어도
    중복을 만들지 않습니다. 인덱스는 두 번째 방어선입니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "conversations" in tables:
        columns = {c["name"] for c in inspector.get_columns("conversations")}
        if "history_synced_at" not in columns:
            with engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE conversations ADD COLUMN history_synced_at TIMESTAMP")
                )
            logger.info("0106: conversations.history_synced_at 추가했습니다.")

    if "customer_interactions" in tables:
        names = {ix["name"] for ix in inspector.get_indexes("customer_interactions")}
        if "ux_interaction_external_id" not in names:
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        "CREATE UNIQUE INDEX ux_interaction_external_id "
                        "ON customer_interactions (external_id)"
                    ))
                logger.info("0106: customer_interactions.external_id 에 유니크를 걸었습니다.")
            except Exception:
                # 값이 있는 행에 이미 중복이 있으면 못 겁니다. 수집기가 넣기 전에 확인하므로
                # 인덱스 없이도 중복은 안 생깁니다 — 여기서 이관을 세우지 않습니다.
                logger.warning(
                    "0106: external_id 유니크 인덱스를 못 만들었습니다 (기존 중복). "
                    "수집기는 넣기 전에 확인하므로 동작에는 지장이 없습니다.",
                    exc_info=True,
                )
