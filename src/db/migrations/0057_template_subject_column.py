"""메일 제목을 그 메일의 행 안으로 — ``email_templates.subject``.

0056 은 접수확인 제목을 **별도 행**(``auto_ack_subject``)으로 두었습니다. 그러면 목록에
"자동 접수확인" 과 "접수확인 제목" 이 따로 서고, 한 메일을 고치는 데 두 화면을 오가야
합니다. 제목과 본문은 한 메일의 두 부분이지 두 개의 템플릿이 아닙니다.

그래서 열로 옮깁니다. 0056 이 심은 값은 그대로 옮겨 담고 그 행들은 지웁니다 — 옮기지 않으면
운영자가 이미 고쳐 둔 문구가 사라지고, 지우지 않으면 목록에 아무도 안 읽는 행이 남습니다.

제목이 있는 메일은 접수확인뿐입니다. 서명·링크·담당자 이름에는 제목이라는 것이 없고,
답변 메일 형식은 뼈대일 뿐 메일이 아닙니다. 그래서 nullable 이고, 화면도 제목이 의미 있는
행에서만 그 칸을 보여줍니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

# (제목을 옮겨 담을 행, 0056 이 만든 제목 행)
_MOVES = (("auto_ack", "auto_ack_subject"), ("auto_ack_en", "auto_ack_subject_en"))


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "email_templates" not in set(inspector.get_table_names()):
        logger.info("0057: email_templates missing; skipping.")
        return

    columns = {column["name"] for column in inspector.get_columns("email_templates")}
    with engine.begin() as conn:
        if "subject" not in columns:
            conn.execute(text("ALTER TABLE email_templates ADD COLUMN subject TEXT"))

        for target, source in _MOVES:
            row = conn.execute(
                text("SELECT body FROM email_templates WHERE key = :k"), {"k": source}
            ).first()
            if row is None:
                continue
            conn.execute(
                text("UPDATE email_templates SET subject = :subject WHERE key = :k"),
                {"subject": row[0], "k": target},
            )
            conn.execute(text("DELETE FROM email_templates WHERE key = :k"), {"k": source})
            logger.info("0057: %s.subject <- %s", target, source)
