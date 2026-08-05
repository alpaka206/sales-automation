"""이름에서 언어를 뺍니다 — 언어는 이미 칸입니다.

심어 둔 이름이 제각각이었습니다:

    자동 접수확인 (한국어 원본)   담당자 이름 (한국어)
    자동 접수확인 (영어)          담당자 이름 (영문)

목록은 언어가 같은 템플릿을 한 줄로 묶고 언어는 따로 보여주므로, 이름에 또 적으면 같은 것을
두 번 말하는 셈입니다. 게다가 화면이 이름 뒤의 언어 표기를 떼어내고 있었는데 "(영문)" 과
"(한국어 원본)" 은 그 목록에 없어서 어떤 줄은 떼이고 어떤 줄은 안 떼였습니다 — 한 화면에
"담당자 이름" 과 "담당자 이름 (영문)" 이 같이 보였습니다.

떼는 규칙을 늘리는 대신 이름에서 없앱니다. 표기가 몇 가지든 상관없어지고, 화면에서 떼어내는
코드도 사라집니다.

**서명은 건드리지 않습니다.** 서명 이름은 담당자가 직접 지은 것이고, 거기 들어간 "(한국어)"
는 언어 칸의 중복이 아니라 그 사람이 고른 이름의 일부일 수 있습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_NAMES = {
    "auto_ack": "자동 접수확인",
    "auto_ack_en": "자동 접수확인",
    "sender_name": "담당자 이름",
    "sender_name_en": "담당자 이름",
}


def up(engine: Engine) -> None:
    if "email_templates" not in set(inspect(engine).get_table_names()):
        logger.info("0059: email_templates missing; skipping.")
        return
    with engine.begin() as conn:
        for key, name in _NAMES.items():
            conn.execute(
                text("UPDATE email_templates SET name = :name WHERE key = :key"),
                {"name": name, "key": key},
            )
    logger.info("0059: template names no longer repeat the language column.")
