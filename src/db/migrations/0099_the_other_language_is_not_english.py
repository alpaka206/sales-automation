"""언어 칸의 ``en`` 을 ``foreign`` 으로 (2026-08-27 운영자 지시).

「영어」가 아니라 **「외국어」**입니다. ``_en`` 행을 고르는 조건이 「영어인가」가 아니라
「한국어가 **아닌가**」이기 때문입니다 — ``prompts.get_reply_format`` 은
``not language.startswith("ko")`` 로 가르고, 일본어 문의도 베트남어 문의도 그 행을 읽습니다.
화면에 「영어」라고 적혀 있으면 그 행을 영어 전용으로 읽게 되고, 실제로 무엇이 그 행을 읽는지
와 어긋납니다.

**키는 안 바꿉니다.** ``reply_format_en`` · ``meeting_link_en`` · ``sender_name_en`` 은
발송 경로가 정확한 이름으로 꺼내 가는 **코드 참조**입니다(``prompts.py`` 의 ``f"{key}_en"``).
옮기는 순간 조회의 답이 없어집니다. 바꾸는 것은 화면에 뜨는 ``language`` 칸 하나입니다.

**그 칸을 읽는 코드는 없습니다.** ``get_email_template(key, language)`` 이 유일한 소비자인데
그 함수의 docstring 이 적어 두었듯 **아무 호출자도 language 를 넘기지 않습니다** — 언어는
키에 살고, 발송 경로는 키로 고릅니다. 그래서 이 값은 지금도 앞으로도 화면에 보여 주는
글자이고, 무엇으로 적든 동작이 안 바뀝니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    if "email_templates" not in set(inspect(engine).get_table_names()):
        logger.info("0099: email_templates 없음, 건너뜁니다.")
        return
    with engine.begin() as conn:
        moved = conn.execute(
            text("UPDATE email_templates SET language = 'foreign' WHERE language = 'en'")
        ).rowcount
    logger.info("0099: 언어 칸 'en' → 'foreign' (%s행).", moved)
