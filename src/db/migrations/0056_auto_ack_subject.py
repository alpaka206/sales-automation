"""접수확인 메일의 제목을 고정 문구로 — ``auto_ack_subject`` / ``auto_ack_subject_en``.

지금까지 제목은 ``RE: <고객이 쓴 제목>`` 이었습니다. 그래야 고객 메일함에서 원래 메일과
한 스레드로 묶이기 때문인데, 운영자의 결정은 접수확인만은 **정해진 문구**로 나가는 것입니다:

    [Perso Dubbing] B2B 문의 접수가 완료되었습니다.

대가는 알고 넘어갑니다: 이 메일은 고객 쪽에서 **원래 문의와 다른 대화**로 뜹니다. 이후
담당자의 상세 회신은 여전히 ``RE:`` 라 그쪽은 원래 스레드에 붙습니다.

언어가 정확히 맞는 행이 있을 때만 씁니다. 프랑스어 문의에 한국어 제목이 붙는 것이 제목이
없는 것보다 나쁘므로, 없는 언어는 예전처럼 ``reply_subject`` 가 만든 그 언어의 제목으로
떨어집니다(제목 없는 문의용 기본 문구도 그 안에 언어별로 있습니다).
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_ROWS = (
    ("auto_ack_subject", "접수확인 제목 (한국어)", "ko", "[Perso Dubbing] B2B 문의 접수가 완료되었습니다."),
    ("auto_ack_subject_en", "접수확인 제목 (영어)", "en", "[Perso Dubbing] We've received your inquiry"),
)


def up(engine: Engine) -> None:
    if "email_templates" not in set(inspect(engine).get_table_names()):
        logger.info("0056: email_templates missing; nothing to seed.")
        return

    ts_default = "CURRENT_TIMESTAMP" if engine.dialect.name == "sqlite" else "NOW()"
    with engine.begin() as conn:
        for key, name, language, body in _ROWS:
            if conn.execute(
                text("SELECT 1 FROM email_templates WHERE key = :k"), {"k": key}
            ).first():
                continue
            conn.execute(
                text(
                    "INSERT INTO email_templates (key, name, language, channel, body, "
                    "description, status, version, created_at, updated_at) "
                    "VALUES (:key, :name, :language, 'email', :body, :description, "
                    f"'active', 1, {ts_default}, {ts_default})"
                ),
                {
                    "key": key,
                    "name": name,
                    "language": language,
                    "body": body,
                    "description": "접수확인 메일의 제목. 비우면 'RE: 고객 제목'으로 나갑니다.",
                },
            )
            logger.info("0056: seeded %s", key)
