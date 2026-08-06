"""서명에는 언어가 없습니다.

무엇이 그 값을 읽었나: 아무것도요. 어떤 코드도 언어로 서명을 고르지 않습니다 — 고르는 것은
사람이고, 그 자리는 검토 화면입니다(0061). 목록의 정렬 기준에 끼어 있었고, 고르개에 `· ko`
라는 꼬리표를 달았고, 편집 화면이 답이 없는 질문을 하나 더 물었을 뿐입니다.

화면에서는 이미 다 뺐습니다. 여기서는 **값 자체**를 없앱니다. 안 그러면 ``signature_ko`` 는
아무도 못 보고 아무도 못 바꾸는 ``language='ko'`` 를 계속 들고 있게 되고, 언젠가 그 값을
읽는 화면이 다시 생깁니다 — 지금 아무 뜻도 없는 그 값을요.

``email_templates.language`` 열은 남습니다. ``auto_ack`` / ``auto_ack_en`` 은 정말로 한 메일의
두 언어입니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    if "email_templates" not in set(inspect(engine).get_table_names()):
        logger.info("0063: email_templates missing; skipping.")
        return

    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE email_templates SET language = 'all' "
                "WHERE key LIKE 'signature\\_%' ESCAPE '\\' AND language <> 'all'"
            )
        )
    logger.info("0063: %s signature(s) lost a language nothing read.", result.rowcount)
