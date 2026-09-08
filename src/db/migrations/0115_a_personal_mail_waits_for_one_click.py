"""``mailbox_link_decisions`` — 개인함 메일을 티켓에 붙일지 정한 기록 (2026-09-08 지시).

개인 사서함으로 온 메일 중 **우리가 아는 연락처의 것**만 남기고, 그 연락처에 티켓이 있으면
운영자에게 묻습니다: 「untae@estsoft.com 으로 ○○ 님 메일이 왔습니다 — △△ 티켓에
연결할까요?」

**왜 사람이 눌러야 하나.** 붙이는 코드는 이미 있고(`ticket_history.attach_personal_emails`),
안 도는 이유가 딱 하나였습니다 — 그 함수의 주석 그대로: 「그 연락처에 티켓이 하나일 때만
붙입니다. 여럿이면 그 메일이 어느 대화의 것인지 우리가 알 수 없고, **잘못 붙으면 남의
티켓에 남의 메일이 서는데 아무도 그것이 잘못됐다는 것을 모릅니다.**」 그 한 번의 판단을
사람이 합니다.

**이 표는 「물어봤고 답을 들었다」만 담습니다.** 메일 자체는 `customer_interactions` 에
들어가 있고(연락처 단위, `conversation_id` 가 비어 있음), 「연결」은 그 칸을 채우는
일입니다. 결정을 안 적으면 **거절한 메일이 회차마다 다시 물어봅니다.**

표를 따로 두는 이유: `customer_interactions` 는 이 앱에서 가장 붐비는 표이고(허브스팟
수집기가 메일·채팅·폼을 전부 여기 넣습니다), 그 표에 이 기능만 쓰는 칸을 더하면 읽는
코드 전부가 그 칸을 지나게 됩니다. 여기 있는 줄은 운영자가 버튼을 누른 횟수만큼입니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    if "mailbox_link_decisions" in set(inspect(engine).get_table_names()):
        return
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mailbox_link_decisions (
                external_id VARCHAR(255) PRIMARY KEY,
                conversation_id INTEGER,
                decided_by VARCHAR(320),
                decided_at TIMESTAMP NOT NULL
            )
        """))
    logger.info("0115: mailbox_link_decisions 를 만들었습니다.")
