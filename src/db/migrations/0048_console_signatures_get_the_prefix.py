"""콘솔에서 만든 서명이 서명으로 취급되지 않던 것을 고칩니다.

08-04 이전에는 저장 키를 담당자가 직접 입력했습니다(그 폼은 137081c 에서 없앴고, 지금은
이름에서 ``signature_html_`` 접두사를 붙여 자동 생성합니다). 그 이전에 만든 서명은 접두사가
없어서, 서명을 찾는 **두 곳이 모두 그 행을 보지 못합니다**:

    list_signature_templates()   회신 화면의 서명 선택기
    default_signature_key()      새 초안에 기본으로 찍히는 서명

둘 다 ``key LIKE 'signature\\_html\\_%'`` 로 찾습니다. 그래서 담당자가 직접 쓴 서명이 어느
초안에도 붙일 수 없는 상태로 남아 있었고, 화면에서도 서명이 아니라 '이메일 템플릿' 으로
묶여 보였습니다 — 묶는 기준(``_template_kind``)도 키이기 때문입니다.

**무엇을 옮기는가**: 코드가 이름으로 찾는 키를 뺀 나머지 전부. 발송 경로는 템플릿을 정확한
키로 읽으므로 코드에 없는 키를 가진 행은 아무것도 읽을 수 없는 행이고, 그런 행을 만들 수
있었던 통로는 콘솔의 '새로 만들기' 하나뿐이며 그건 서명만 만듭니다. 아래 목록은 이 시점의
코드가 참조하는 키 전부이고, 마이그레이션이므로 그 시점 그대로 굳습니다.

``messages.signature_key`` 도 같이 옮깁니다. 이미 그 키로 저장된 초안이 있으면 키만 바뀌어
서명이 조용히 사라지기 때문입니다.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_PREFIX = "signature_html_"

# 코드가 정확한 키로 읽는 행들 — 서명이 아닙니다. (0019/0025/0042 에서 심은 것 전부)
_CODE_KEYS = (
    "auto_ack",
    "greeting",
    "footer_note",
    "reply_format",
    "meeting_link",
    "whatsapp_link",
    "signature_ko",
    "signature_en",
)


def _slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def up(engine: Engine) -> None:
    if "email_templates" not in set(inspect(engine).get_table_names()):
        logger.info("0048: email_templates missing; skipping.")
        return

    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, key, name FROM email_templates")).fetchall()
        taken = {row[1] for row in rows}
        has_messages = "messages" in set(inspect(engine).get_table_names())

        for row_id, key, name in rows:
            if key in _CODE_KEYS or key.startswith("signature"):
                continue
            base = _slug(key) or _slug(name or "") or str(row_id)
            new_key = f"{_PREFIX}{base}"[:100]
            suffix = 2
            while new_key in taken:
                new_key = f"{_PREFIX}{base}_{suffix}"[:100]
                suffix += 1
            taken.add(new_key)

            conn.execute(
                text("UPDATE email_templates SET key = :new WHERE id = :id"),
                {"new": new_key, "id": row_id},
            )
            if has_messages:
                conn.execute(
                    text("UPDATE messages SET signature_key = :new WHERE signature_key = :old"),
                    {"new": new_key, "old": key},
                )
            logger.info("0048: %s -> %s (%s)", key, new_key, name)
