"""0082 을 되돌립니다 — 가져온 기록은 티켓에 붙이지 않습니다.

0082 는 허브스팟에서 끌어온 연락처 단위 기록을 「그 시각에 열려 있던 가장 최근 문의」로
티켓에 붙였습니다. 틀린 규칙이었습니다:

  * **New 티켓에 남의 옛 이야기가 붙었습니다** — 실측 20건. New 는 아직 아무 일도 안
    일어난 티켓이라 「이 티켓의 기록」이 있을 수 없는데, 몇 달 전 메일이 그 티켓의
    기록으로 그려졌습니다(2026-08-20 운영자 지적).
  * 기록 434건 중 64건이 **그 대화가 생기기도 전**의 것이었습니다. 백필로 주워 온 티켓은
    `created_at` 이 백필 시각이라 「언제 시작된 티켓인가」를 우리가 모릅니다 — 시간으로
    가를 수 있다는 전제 자체가 틀렸습니다.

허브스팟은 그 기록이 어느 티켓 것인지 알려 주지 않습니다. 모르는 것을 짐작해 붙이면
화면은 확신에 차서 틀린 말을 합니다. **가져온 기록은 「이 고객의 기록」에 삽니다** —
거기서는 그 사람과 오간 전부라는 뜻이라 틀릴 수가 없습니다.

사람이 콘솔에서 적은 기록은 폼이 티켓을 같이 받으므로 그대로 둡니다(`external_id` 가
없는 행). 지우는 것은 가져온 행의 연결뿐입니다.

**뒷이야기**: 같은 날 알아낸 것이 있습니다 — 허브스팟은 메일마다 **티켓 연결을 실제로
가지고 있습니다**(`emails/{id}/associations/tickets`, 표본 9건 중 8건). 우리가 안 읽고
있었을 뿐입니다. 그래서 지금은 그 값으로 붙입니다. 이 이관이 되돌린 것은 **짐작으로 붙인
연결**이고, 다음 동기화가 **허브스팟이 알려 준 연결**로 다시 채웁니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    if "customer_interactions" not in set(inspect(engine).get_table_names()):
        logger.info("0084: customer_interactions 없음, 건너뜁니다")
        return
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE customer_interactions SET conversation_id = NULL "
                "WHERE external_id IS NOT NULL AND conversation_id IS NOT NULL"
            )
        )
        logger.info("0084: 가져온 기록 %d건의 티켓 연결을 풀었습니다.", result.rowcount)
