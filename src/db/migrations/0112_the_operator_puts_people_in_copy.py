"""``messages.cc_addresses`` — 회신에 참조를 건다 (2026-09-07 운영자 지시).

**나가는 주소(`To`)와 보내는 주소(`From`)는 하나도 안 건드립니다.** 이 칸은 그 위에
얹히기만 합니다 — NULL 이 「참조 없음」이고, 손대지 않은 초안은 예전과 **바이트 단위로
같은 payload** 로 나갑니다(`tests/test_reply_cc.py` 가 그것을 고정합니다).

**됩니까**: 됩니다. 문서가 아니라 이 포털에서 실제로 나간 메시지로 확인했습니다
(2026-09-07, 스레드 600개·메시지 666건 읽기 전용 조사): `recipientField` 가 `TO` 546 ·
`BCC` 6 · `CC` 5 이고, CC 가 붙은 **OUTGOING** 이 4건 있습니다. 모양은 우리가 이미 보내는
`TO` 와 같습니다 — `HS_EMAIL_ADDRESS` + 주소, `actorId` 없음. 한 메시지에 CC 넷도 있어
여러 명이 되는 것도 확인했습니다. (CLAUDE.md: 「고칠 때 기준으로 삼을 것은 문서도 actor
조회도 아니고 그 포털에서 실제로 나간 메시지다」.)

`channel_account_id`(0105)와 **같은 길**을 다닙니다: 고르개 → 라우트 → 열 → 발송.
값이 사는 곳이 하나라 화면과 발송이 갈릴 자리가 없습니다.

문자열 하나에 쉼표로 잇습니다. 표를 따로 만들지 않는 이유는 `to_address` 와 같습니다 —
조회 조건으로 쓰는 값이 아니고, 이 메시지가 나갈 때 한 번 읽혀 payload 가 되고 끝입니다.
철자를 다듬는 곳은 `senders.parse_cc_addresses` **한 곳**이라 저장된 모양과 나가는 모양이
같습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "messages" not in set(inspector.get_table_names()):
        logger.info("0112: messages 없음, 건너뜁니다.")
        return
    if "cc_addresses" in {c["name"] for c in inspector.get_columns("messages")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE messages ADD COLUMN cc_addresses VARCHAR(1000)"))
    logger.info("0112: messages.cc_addresses 를 더했습니다.")
