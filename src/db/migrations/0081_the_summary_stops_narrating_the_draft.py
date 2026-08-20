"""초안을 보고 쓴 티켓 요약을 비웁니다 — 일회성 정리.

요약은 늘 **초안이 만들어진 직후**에 쓰였습니다(`inbound.handle` → `_update_summary`).
그 함수는 그 대화의 모든 메시지를 읽었고 초안도 메시지 행이라, 아무도 보내지 않은 글이
「이에 Perso AI 는 …라고 안내했습니다」로 요약에 들어갔습니다. 운영자는 이 요약을 읽고 다음
답을 씁니다 — 「이 얘기는 이미 했으니 생략」이 되어 버립니다(2026-08-20 지적).

**나간 답을 묘사한 요약은 하나도 없습니다.** 요약을 쓰는 길이 저 한 곳뿐이었고 그 자리는
SMTP 앞이기 때문입니다. 그래서 고를 것이 없어 전부 비웁니다.

이제 요약은 `agents/summaries.append_summary_line` 이 **일어난 일마다 한 줄씩** 붙입니다 —
문의가 저장될 때 한 줄, 답이 **정말 나갔을 때** 한 줄. 앞 줄은 고치지 않습니다. 그래서 이
이관이 비운 자리는 다음 일이 생기는 대로 스스로 다시 찹니다.

`customer_requests` 는 그대로 둡니다. 그건 **고객이 쓴 글**에서 뽑은 것이라 초안과 상관이
없고, 지금은 그 추출이 inbound 메시지만 읽습니다(`_extract_requests`).
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    if "conversations" not in set(inspect(engine).get_table_names()):
        logger.info("0081: conversations 없음, 건너뜁니다")
        return
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE conversations SET summary = NULL "
                "WHERE summary IS NOT NULL AND trim(summary) <> ''"
            )
        )
        if result.rowcount:
            logger.info("0081: 초안으로 쓴 티켓 요약 %d건을 비웠습니다.", result.rowcount)
        else:
            logger.info("0081: 비울 티켓 요약이 없습니다.")
