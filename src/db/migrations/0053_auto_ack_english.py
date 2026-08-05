"""접수확인 메일의 영어 원본.

지금까지 접수확인은 **한국어 원본을 기계번역해서** 나갔습니다. 문의의 90%가 영어인데
그 90%가 전부 번역문이었다는 뜻이고, 번역은 Gemini 호출 한 번(접수확인은 사람 승인 없이
바로 나가므로 그 지연이 고객이 기다리는 시간에 그대로 붙습니다) 게다가 매번 조금씩 다르게
나옵니다 — 같은 문장이어야 할 자동 메일이.

키를 ``auto_ack_en`` 으로 나눕니다 — ``signature_ko`` / ``signature_en`` 이 이미 그 모양이고,
``email_templates.key`` 에 UNIQUE 가 걸려 있어 한 키가 언어별로 여러 행을 가질 수 없습니다.
(``get_email_template`` 의 language 인자는 한 키 안의 ``all`` 대체용입니다.)

영어 문의는 이 문장을 **그대로** 쓰고, 그 외 언어는 예전처럼 한국어를 번역합니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_EN = (
    "Hi {name},\n\n"
    "Thank you for reaching out to Perso Dubbing. Your message has arrived safely, "
    "and our team is reviewing it now — we'll get back to you with a full reply shortly.\n\n"
    "Best regards,"
)


def up(engine: Engine) -> None:
    if "email_templates" not in set(inspect(engine).get_table_names()):
        logger.info("0053: email_templates missing; nothing to seed.")
        return

    ts_default = "CURRENT_TIMESTAMP" if engine.dialect.name == "sqlite" else "NOW()"
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM email_templates WHERE key = 'auto_ack_en'")
        ).first()
        if exists:
            return
        conn.execute(
            text(
                "INSERT INTO email_templates (key, name, language, channel, body, "
                "description, status, version, created_at, updated_at) "
                "VALUES ('auto_ack_en', '자동 접수확인 (영어)', 'en', 'email', :body, "
                f":description, 'active', 1, {ts_default}, {ts_default})"
            ),
            {
                "body": _EN,
                "description": "영어 문의의 접수확인. 번역하지 않고 이 문장을 그대로 씁니다.",
            },
        )
        logger.info("0053: seeded the English auto_ack.")
