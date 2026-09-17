"""``conversations.followup_closed_at`` — 후속 리마인더가 닫은 티켓 (2026-09-17).

**왜 생겼나.** Contacted 에서 답이 없으면 3일 뒤 리마인더, 5일 뒤 마감 메일, 7일 뒤 Closed Lost
로 옮긴다(`agents/followup_sequence`). 그 뒤에 고객이 답하면 Negotiating 으로 되살리고 빨갛게
보여 달라는 것이 운영자 요청인데, **사람이 Closed Lost 로 옮긴 티켓은 되살리면 안 된다.** 둘은
단계 값만 봐서는 같다 — 이 칸이 「기계가 닫았다」를 든다.

리마인더 자체는 새 칸이 없다: `messages` 행이고 `prompt_variant` 로 가른다. 몇 번째를 보냈는지는
그 행들에서 읽는다 — 저장하면 원본이 바뀔 때 조용히 어긋난다.

**값은 안 옮긴다.** 이 기능이 생기기 전에 닫힌 티켓은 전부 사람이 닫은 것이다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "conversations" not in set(inspector.get_table_names()):
        return
    if "followup_closed_at" in {c["name"] for c in inspector.get_columns("conversations")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE conversations ADD COLUMN followup_closed_at TIMESTAMP"))
    logger.info("0121: conversations.followup_closed_at 를 만들었습니다.")
