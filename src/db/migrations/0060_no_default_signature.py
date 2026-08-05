"""기본 서명을 없앱니다 — 서명은 초안마다 사람이 고릅니다.

0046 이 "누가 회사 메일에 서명하는가" 를 코드의 문자열에서 행의 플래그로 옮겼습니다. 옳은
방향이었지만 한 단계 더 갈 수 있습니다: 그 결정이 **초안마다 이미 있습니다.** 검토 화면의
서명 드롭다운이 그것이고, 거기에는 `기본 (텍스트 서명)` · 각 브랜드 서명 · `서명 없음` 이
전부 있습니다. 기본값은 그 선택의 출발점을 미리 정해 두는 장치일 뿐이었고, 그 대가로
"어느 것이 기본인가" 를 저장하고, 하나뿐임을 인덱스로 보장하고, 옮기는 버튼과 라우트를
두어야 했습니다.

없애면 초안은 ``signature_key = NULL`` 로 시작합니다. 그건 "서명 없음" 이 아니라 **회사
규칙에 정의된 텍스트 서명** 입니다(프롬프트의 ``{{__signature__}}``). 그래서 아무것도 안
고른 메일도 서명이 붙은 채로 나가고, 브랜드 카드가 필요하면 그 건에서 고릅니다.

``is_default`` 열과 유일 인덱스를 지웁니다. 남겨 두면 아무도 안 읽는 값이 화면에 "기본"
이라고 표시될 수 있는 상태로 남습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_INDEX = "ux_email_templates_one_default"


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "email_templates" not in set(inspector.get_table_names()):
        logger.info("0060: email_templates missing; skipping.")
        return

    with engine.begin() as conn:
        try:
            conn.execute(text(f"DROP INDEX IF EXISTS {_INDEX}"))
        except Exception:
            logger.warning("0060: could not drop %s.", _INDEX)

    if "is_default" not in {c["name"] for c in inspector.get_columns("email_templates")}:
        return
    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE email_templates DROP COLUMN is_default"))
        except Exception:
            # 아주 오래된 SQLite. 읽는 곳이 없으므로 남아 있어도 무해합니다.
            logger.warning("0060: could not drop email_templates.is_default; leaving it.")
    logger.info("0060: the default signature is a per-draft choice now.")
