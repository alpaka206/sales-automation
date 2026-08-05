"""담당자 이름의 영문 표기 — ``sender_name_en``.

0051 은 이름을 한 칸으로 뒀습니다. 그런데 한국어 회신은 "이스트소프트 배운태입니다" 이고
영어 회신은 "Untae Bae" 입니다 — 같은 사람의 다른 표기이지 번역할 대상이 아닙니다. 한 칸만
두면 둘 중 하나는 반드시 틀리고, 초안이 한국어로 쓰였다가 발송 전에 번역되는 구조라 모델이
"배운태" 를 알아서 로마자로 바꾸게 됩니다 — 매번 다르게.

두 칸을 두고 **문의 언어로 고릅니다**(``prompts.apply_editable_tokens``). 초안 본문이 아직
한국어여도 고르는 기준은 고객의 언어입니다: 영어 고객에게 갈 초안이면 처음부터 영문 표기가
들어가고, 번역 단계가 그것을 건드리지 않습니다.

비운 채로 심습니다 — 0051 과 같은 이유로, 값이 없으면 토큰이 초안에 남아 발송 전에 보입니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_KEY = "sender_name_en"


def up(engine: Engine) -> None:
    if "email_templates" not in set(inspect(engine).get_table_names()):
        logger.info("0055: email_templates missing; nothing to seed.")
        return

    ts_default = "CURRENT_TIMESTAMP" if engine.dialect.name == "sqlite" else "NOW()"
    with engine.begin() as conn:
        # 0051 의 행은 이제 "한국어 표기" 입니다. 이름표만 그렇게 맞춥니다.
        conn.execute(
            text(
                "UPDATE email_templates SET name = '담당자 이름 (한국어)', language = 'ko' "
                "WHERE key = 'sender_name'"
            )
        )
        if conn.execute(text("SELECT 1 FROM email_templates WHERE key = :k"), {"k": _KEY}).first():
            return
        conn.execute(
            text(
                "INSERT INTO email_templates (key, name, language, channel, body, "
                "description, status, version, created_at, updated_at) "
                "VALUES (:key, '담당자 이름 (영문)', 'en', 'email', '', :description, "
                f"'active', 1, {ts_default}, {ts_default})"
            ),
            {
                "key": _KEY,
                "description": "영어 회신의 {{SENDER_NAME}} 이 이 이름으로 치환됩니다.",
            },
        )
        logger.info("0055: seeded %s (empty on purpose).", _KEY)
