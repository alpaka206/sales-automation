"""접수확인 아래에는 서명이 아니라 로고가 붙습니다.

접수확인은 "받았습니다" 한 문장이고 아직 사람이 읽지도 않은 메일입니다. 거기에 담당자
서명을 붙이면 누가 봐도 그 사람이 쓴 메일로 읽히는데, 정작 답은 며칠 뒤에 다른 사람이
쓸 수도 있습니다. 서명은 **첫 답변 초안**부터 — 그건 사람이 검토하고 발송을 누릅니다.

붙는 자리와 방법은 서명과 같습니다: ``messages.signature_key`` 가 가리키는 템플릿이
발송 시점에 본문 아래로 들어갑니다. 그래서 새 기계가 필요 없고, 로고를 바꾸는 것도
콘솔의 이메일 템플릿 한 줄을 고치는 일입니다. 키에 ``signature_`` 접두사를 붙이지 않은
것이 요점입니다 — 붙였으면 검토 화면 서명 목록에 로고가 하나 끼어 있었을 겁니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_KEY = "auto_ack_footer"
_NAME = "접수확인 하단 로고"

# 링크는 글자 없이 로고만 — ``font-size: 0px`` 가 앵커 안의 공백까지 지웁니다.
_BODY = (
    '<a href="https://perso.ai/dubbing" target="_blank" rel="noreferrer" '
    'style="font-size: 0px; text-decoration: none;">'
    '<img src="https://framerusercontent.com/images/'
    'qsb78edMTO0lCrOpPgaEEtrpmo.svg?width=1752&amp;height=279" '
    'height="28" alt="Perso Dubbing" style="border: 0px;">'
    "</a>"
)


def up(engine: Engine) -> None:
    if "email_templates" not in set(inspect(engine).get_table_names()):
        logger.info("0062: email_templates missing; nothing to seed.")
        return

    ts_default = "CURRENT_TIMESTAMP" if engine.dialect.name == "sqlite" else "NOW()"
    with engine.begin() as conn:
        if conn.execute(text("SELECT 1 FROM email_templates WHERE key = :k"), {"k": _KEY}).first():
            return
        conn.execute(
            text(
                "INSERT INTO email_templates (key, name, language, channel, body, "
                "description, status, version, created_at, updated_at) "
                f"VALUES (:key, :name, 'all', 'email', :body, :description, 'active', 1, "
                f"{ts_default}, {ts_default})"
            ),
            {
                "key": _KEY,
                "name": _NAME,
                "body": _BODY,
                "description": "접수확인 메일 본문 아래에 붙는 로고. 비우면 아무것도 붙지 않습니다.",
            },
        )
    logger.info("0062: seeded the auto-ack footer logo.")
