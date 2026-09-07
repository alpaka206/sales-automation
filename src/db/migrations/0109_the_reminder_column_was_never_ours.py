"""`reminder_sent` 단계를 없앱니다 (2026-09-07 운영자 지시).

**리마인더는 이 앱이 보내지 않습니다** — 허브스팟 워크플로가 보냅니다. 그래서 보드에는
아무도 옮기지 않는 열이 하나 서 있었고, 워크북에는 그 단계를 적을 말이 아예 없었습니다
(`google_sheets._STAGE_VALUES` 에 `reminder_sent` 만 빠져 있었고, 그 단계로 옮기면 시트가
옛 값을 그대로 든 채 경고만 남았습니다).

**남아 있던 행은 `negotiation` 으로 옮깁니다.** 리마인더는 아직 진행 중인 건에 나가는
것이고, `negotiation` 이 이 파이프라인에서 「아직 일하고 있다」를 뜻하는 칸입니다
(0040 이 같은 말로 정리했습니다). 그대로 두면 화면이 그 행에 raw 키를 그립니다 —
`PIPELINE_STAGES` 에 없는 값은 표시 이름이 없습니다.

옮기는 열은 **둘**입니다. 문의별(`conversations.stage`)과 연락처별
(`customer_profiles.pipeline_stage`) — 화면이 자리마다 다른 쪽을 읽으므로(보드는 앞엣것,
리드 히스토리·고객 상세는 뒤엣것) 한쪽만 옮기면 같은 건이 화면마다 다른 단계로 보입니다.

**허브스팟 파이프라인에서도 같이 없앱니다** (운영자, 2026-09-07). 그래서 접어 둘 stage id
자체가 없어지고, 설정 `HUBSPOT_TICKET_STAGE_REMINDER_SENT` 도 같이 나갔습니다.

순서만 주의하면 됩니다: 허브스팟에서 단계를 지우면 그 자리에 있던 티켓을 **다른 단계로
옮기라고 저쪽이 먼저 묻습니다.** 그 뒤에는 우리가 모르는 stage id 가 남지 않습니다.
그 전에 이 이관이 먼저 돌아도 무해합니다 — 옮겨 둔 행은 다음 동기화가 허브스팟이 정한
새 단계로 다시 맞춥니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_MOVES = (
    ("conversations", "stage"),
    ("customer_profiles", "pipeline_stage"),
)


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        for table, column in _MOVES:
            if table not in tables:
                continue
            moved = conn.execute(
                text(f"UPDATE {table} SET {column} = 'negotiation' "  # noqa: S608 — 상수 목록
                     f"WHERE {column} = 'reminder_sent'")
            ).rowcount
            if moved:
                logger.info("0109: %s.%s %d행을 negotiation 으로 옮겼습니다.", table, column, moved)
