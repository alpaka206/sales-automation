"""담당자 이름을 회신 본문에서 한 곳으로 뺍니다 — ``{{SENDER_NAME}}``.

한국어 기본 메일 템플릿은 본문 첫 줄에서 쓰는 사람을 이름으로 소개합니다
("이스트소프트 OOO입니다"). 그 자리를 정책 문서에 이름으로 박아 두면 담당자가 바뀔 때
고칠 곳이 서명과 문서 두 군데가 되고, 한쪽만 고치면 서명과 인사말이 서로 다른 사람을
가리키는 메일이 나갑니다. 비워 두면 모델이 지어냅니다.

그래서 링크 두 개와 같은 방식으로 다룹니다: 문서에는 토큰을 두고, 발송 직전에 이 행의
값으로 치환합니다(``prompts.apply_editable_tokens``). 고치는 곳은 이메일 템플릿 한 곳입니다.

**비워 둔 채로 심습니다.** 값이 없으면 토큰이 본문에 그대로 남아 검토 화면에 보입니다 —
"이스트소프트 입니다" 는 읽는 순간 이미 나간 뒤지만, 토큰은 발송 전에 눈에 띕니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_KEY = "sender_name"


def up(engine: Engine) -> None:
    if "email_templates" not in set(inspect(engine).get_table_names()):
        logger.info("0051: email_templates missing; nothing to seed.")
        return

    ts_default = "CURRENT_TIMESTAMP" if engine.dialect.name == "sqlite" else "NOW()"
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM email_templates WHERE key = :key"), {"key": _KEY}
        ).first()
        if exists:
            return
        conn.execute(
            text(
                "INSERT INTO email_templates (key, name, language, channel, body, "
                "description, status, version, created_at, updated_at) "
                "VALUES (:key, '담당자 이름', 'all', 'email', '', :description, "
                f"'active', 1, {ts_default}, {ts_default})"
            ),
            {
                "key": _KEY,
                "description": "회신 본문의 {{SENDER_NAME}} 토큰이 이 이름으로 치환됩니다.",
            },
        )
        logger.info("0051: seeded email template %s (empty on purpose).", _KEY)
