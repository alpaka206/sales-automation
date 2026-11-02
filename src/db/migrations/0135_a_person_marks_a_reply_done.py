"""``done_replies`` — 답변 대기에서 사람이 [완료]로 뺀 고객 회신 (2026-11-02).

대화가 끝나 답이 필요 없는 회신을 내릴 길이 「메일 발송」으로 초안을 만들어 거절하는 것뿐이었습니다(안 보낼 초안에
모델을 한 번 부르고, 거절한 초안이 「발송 완료」 탭에 남았습니다). 뺀 회신 하나마다 한 행이고, 행이 없으면 답변
대기에 그대로 섭니다 — 씨앗도 옮길 값도 없습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    # 새 DB 는 0001 의 create_all 이 지금 모델로 이미 만들었습니다.
    if "done_replies" in set(inspect(engine).get_table_names()):
        return
    serial = "INTEGER PRIMARY KEY AUTOINCREMENT" if engine.dialect.name == "sqlite" else "SERIAL PRIMARY KEY"
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE done_replies (
                id {serial},
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                source_ref VARCHAR(64) NOT NULL,
                done_by VARCHAR(255),
                done_at TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text("CREATE UNIQUE INDEX ux_done_replies_reply ON done_replies (conversation_id, source_ref)"))
    logger.info("0135: done_replies 를 만들었습니다.")
