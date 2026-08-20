"""단계 이동으로 쌓인 진행 기록을 지웁니다 — 일회성 정리.

두 종류입니다:

  * ``stage`` — 「HubSpot에서 단계 변경 감지: new → meeting_link_sent」. 화면에서는 이미
    숨기고 있었습니다(`messages._ROUTINE_PROGRESS_KINDS`). 지금 단계는 Stage 칸이 그대로
    보여 주므로, 아무도 안 읽는 줄이 대화마다 쌓이고 있었습니다.
  * ``draft_retired`` — 「단계가 …로 이동해 대기 중이던 초안 1건을 종료 처리했습니다」.
    이건 화면에 보였습니다. 그런데 **이 고객과 오간 일이 아니라 우리 안의 사정**이고,
    히스토리는 「무엇이 오갔나」를 보는 자리입니다(2026-08-20 운영자 지시: 「단계가
    이동했다 이런 거는 기록 안 남겨도 돼, 히스토리든 어디든」).

이제 `stage_sync` 는 둘 다 쓰지 않습니다. 이 이관은 그 전에 쌓인 것을 치웁니다.

**남기는 것**: 문의 접수 · 답변 발송 완료 · 첫 회신 금액 자동 제거 · 접수확인 발송 실패.
넷 다 이 고객에게 실제로 일어난 일이고, 그래서 진행 기록의 뜻이 「우리가 이 사람에게
무엇을 했나」로 좁혀집니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

KINDS = ("stage", "draft_retired")


def up(engine: Engine) -> None:
    if "conversation_progress" not in set(inspect(engine).get_table_names()):
        logger.info("0080: conversation_progress 없음, 건너뜁니다")
        return
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "DELETE FROM conversation_progress "
                "WHERE kind IN ('stage', 'draft_retired')"
            )
        )
        if result.rowcount:
            logger.info("0080: 단계 이동 기록 %d건을 지웠습니다.", result.rowcount)
        else:
            logger.info("0080: 지울 단계 이동 기록이 없습니다.")
