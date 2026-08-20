"""`messages.summary_line` — 그 메시지 한 통을 줄인 한 줄.

티켓 요약(`conversations.summary`)은 이 줄들을 붙여 만든 것입니다. 그런데 화면은 **줄마다**
그것이 어느 메일인지 알아야 합니다: New 를 지난 티켓에서 문의·회신은 통째로 펼쳐 두는 것이
아니라 한 줄로 보이고, 「전체보기」를 눌렀을 때 그 메일이 나와야 하기 때문입니다
(2026-08-20 운영자 지시).

요약 문자열을 줄 단위로 잘라 메시지 순서에 맞춰 세는 방법도 있지만, 한 줄이라도 못 만들면
(모델 호출 실패) 그 뒤가 전부 한 칸씩 밀립니다. 줄은 그 메시지의 것이므로 그 행에 둡니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "messages" not in set(inspector.get_table_names()):
        logger.info("0083: messages 없음, 건너뜁니다")
        return
    if "summary_line" in {c["name"] for c in inspector.get_columns("messages")}:
        logger.info("0083: summary_line 이미 있습니다.")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE messages ADD COLUMN summary_line VARCHAR(300)"))
    logger.info("0083: messages.summary_line 를 추가했습니다.")
