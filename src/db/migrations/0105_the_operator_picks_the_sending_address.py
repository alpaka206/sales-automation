"""회신이 **어느 주소에서** 나갈지 운영자가 고릅니다 (2026-09-02 운영자 지시).

예전에는 고를 수 없었습니다. From 을 정하는 것은 HubSpot 채널 계정인데
(`HUBSPOT_SENDER_ACTOR_ID` 는 감사용 액터라 From 을 안 정합니다 — `config.py` 주석),
그 계정을 고르는 곳이 `find_conversation_reply_context` 안이었고 그 함수가 받는 것은
**티켓 id 와 수신자 주소 둘뿐**이었습니다. 스레드에 이미 있던 메시지의 계정을 그대로
베끼거나, 그것이 없으면 배포 전체가 공유하는 설정값 하나로 떨어졌습니다.

이 칸은 `signature_key` 와 **같은 자리, 같은 모양**입니다: 운영자가 검토 화면에서 고르고,
행에 남고, 발송할 때 쓰입니다. 서명이 이미 그 길을 다니고 있어서 새로 낼 길이 없습니다.

**NULL 이 기본이고 「예전처럼」이라는 뜻입니다.** 이관이 채우는 값은 없습니다 — 손대지
않은 행은 동작이 한 글자도 안 바뀝니다.

주소가 아니라 **채널 계정 id** 를 듭니다: 같은 주소가 인박스마다 따로 연결될 수 있고,
발송 payload 가 받는 것도 id 입니다(`channelAccountId`). `messages.from_address` 를 쓰지
않는 이유는 그 칸이 목록에 **주소로** 그려지기 때문입니다(`messages.py` 의 스레드 목록) —
거기 번호를 넣으면 메일 주소 자리에 숫자가 뜹니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "messages" not in set(inspector.get_table_names()):
        logger.info("0105: messages 없음, 건너뜁니다.")
        return
    if "channel_account_id" in {c["name"] for c in inspector.get_columns("messages")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE messages ADD COLUMN channel_account_id VARCHAR"))
    logger.info("0105: messages.channel_account_id 추가했습니다.")
