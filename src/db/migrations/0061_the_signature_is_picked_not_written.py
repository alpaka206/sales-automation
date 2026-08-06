"""서명은 사람이 고르는 것이지, 모델이 본문에 써 넣는 것이 아닙니다.

두 벌이 있었습니다. 하나는 회사 규칙 안의 ``{{__signature__}}`` — 프롬프트가 로드될 때
``signature_ko`` 행을 끼워 넣어 모델이 누군가의 이름과 메일 주소를 **본문에** 쓰게 했습니다.
다른 하나는 검토 화면의 서명 고르개입니다. 그래서 발송 경로에는 세 번째 기계가 필요했습니다:
운영자가 다른 서명을 고르면 방금 본문에 들어간 그 텍스트를 도로 떼어내는 것
(``strip_known_signature``) — 번역된 메일에서는 메일 주소를 찾아 그 앞을 자르는 방식으로요.

한 벌이면 됩니다. **운영자가 초안에서 고르고 발송을 누르면 그때 붙습니다.** 그러면
``signature_ko``/``signature_en`` 은 코드가 이름으로 찾는 행이 아니라 그냥 서명이 되고,
지울 수 있어야 합니다 — 지울 수 없던 이유가 방금 사라졌기 때문입니다.

이 마이그레이션은 살아 있는 규칙 문서에서 그 조각을 걷어냅니다. 남겨 두면 프롬프트에
``{{__signature__}}`` 이라는 글자와 "아래 서명을 그대로 붙이세요" 라는 문장이 그대로 들어가고,
모델은 붙일 것이 없으니 서명을 **지어냅니다**.

씨앗 파일(``seeds/policy/rule_01_tone.md``)도 같은 문구로 고쳐 두었으므로, 새로 만든
데이터베이스와 이미 돌던 데이터베이스가 같은 문장을 갖습니다. 각 치환은 못 찾으면 아무 일도
하지 않으므로, 운영자가 그 절을 이미 손봤다면 그대로 둡니다.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_NEW_SECTION = (
    "## 시그니처\n\n"
    "**본문에 서명을 쓰지 않습니다.** 이름·직함·회사·연락처를 지어내지 말고, 마지막 줄은 "
    '"감사합니다." 로 끝냅니다. 서명은 운영자가 검토 화면에서 고르고 발송할 때 메일에 '
    "붙습니다.\n"
)

# 순서대로 적용합니다. 첫 번째가 절 전체를 갈아 끼우고, 나머지는 그 절 밖에 흩어져 있던
# 잔재입니다 — 남으면 "위 블록" 이 가리킬 블록이 없는 문장이 됩니다.
_EDITS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^## 시그니처.*?(?=^## |\Z)", re.DOTALL | re.MULTILINE), _NEW_SECTION + "\n"),
    (re.compile(r"^.*\{\{__signature__\}\}.*\n?", re.MULTILINE), ""),
    (re.compile(r"^- 시그니처는 위 블록을.*\n?", re.MULTILINE), ""),
    (re.compile(r"^(\d+\.) 감사 인사와 서명$", re.MULTILINE),
     r"\1 감사 인사 (서명은 쓰지 않습니다 — 운영자가 발송할 때 붙입니다)"),
    (re.compile(r"^# 톤 & 시그니처 \(공통\)$", re.MULTILINE), "# 톤 (공통)"),
    # 본문에는 이제 placeholder 만 남습니다.
    (re.compile(r"^- 본문이나 시그니처에 `\{\{ \}\}`", re.MULTILINE), "- 본문에 `{{ }}`"),
)


def up(engine: Engine) -> None:
    if "policy_sources" not in set(inspect(engine).get_table_names()):
        logger.info("0061: policy_sources missing; skipping.")
        return

    # 키로 고르지 않습니다: 치환은 못 찾으면 no-op 이라 규칙 문서 전체에 돌려도 안전하고,
    # 그러면 이 마이그레이션이 열 이름의 역사(0050 의 notion_page_id → doc_key)를 알 필요가
    # 없습니다.
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, body FROM policy_sources WHERE mode = 'rules'")
        ).fetchall()
        changed = 0
        for row_id, body in rows:
            if not body:
                continue
            new_body = body
            for pattern, replacement in _EDITS:
                new_body = pattern.sub(replacement, new_body)
            if new_body == body:
                continue
            conn.execute(
                text("UPDATE policy_sources SET body = :body WHERE id = :id"),
                {"body": new_body.strip(), "id": row_id},
            )
            changed += 1
    logger.info("0061: the signature left the prompt (%d rule document(s) rewritten).", changed)
