"""Dashboard route — the awaiting-reply queue by tab, and the pipeline board.

The board used to be its own page at /pipeline. It lives here now, below the queue,
because both answer "what needs me next?" and the operator was navigating between them
constantly. /pipeline's POST actions kept their paths; only the page moved.
"""

from __future__ import annotations

from fastapi import APIRouter

from .messages import _messages_list_context

router = APIRouter(tags=["web"])

# The queue panel is a peek, not a list: the five rows that have waited longest. The
# full, filterable list is one click away on 검토 및 회신, so a longer table here only
# pushed the pipeline board off the screen.
_QUEUE_LIMIT = 5


def _dashboard_context() -> dict:
    """Awaiting-reply rows by tab, and the pipeline board."""
    from .customer_ops import (
        MANUAL_LOG_STAGES,
        PIPELINE_STAGES,
        _pipeline_rows,
    )

    # The queue panel IS the 검토 및 회신 list — same query, same row shape, same table
    # partial — sorted oldest-first. Building it here from a second, near-identical query
    # is what let the two tables drift apart before. Uncapped: each tab counts and cuts its own.
    queue = _messages_list_context(status="awaiting", stage="", sort="oldest", limit=None)
    waiting = queue["messages"]
    # **탭은 `awaiting_conversations` 의 두 갈래 그대로다** (2026-11-05 운영자: 「고객 회신도착이랑 발송대기 이런거 다 같은
    # 줄에 있어」 · 「신규 문의, 회신 도착, 전체 이렇게 나누자」). 상태 글자가 아니라 **그 줄이 선 이유**로 가른다 — 고객
    # 답장에 쓰는 초안은 「발송 대기」로 보여도 새 문의가 아니다.
    #
    # **숫자는 그 탭 목록의 총계 그대로고, 줄은 탭마다 따로 자른다.** 전체의 앞 다섯 줄을 화면에서 거르면 오래된 New 가
    # 많은 날 회신 도착이 실제로는 있는데 비어 보인다. 숫자를 따로 세던 쿼리가 두 번 목록과 다른 말을 했다(2026-08-05 의
    # 6 대 1, 2026-08-31 에 목록에만 붙은 수동 초안 예외) — 화면이 여는 목록과 어긋난 숫자는 없는 일감을 찾게 만든다.
    tabs = {
        "new": [row for row in waiting if row["stage"] == "new"],
        "reply": [row for row in waiting if row["stage"] != "new"],
        "all": waiting,
    }

    # Capped per column, with the true per-stage totals alongside — the header must not
    # start under-reporting just because the column stopped rendering every card.
    rows, stage_totals = _pipeline_rows()
    by_stage: dict[str, list] = {stage: [] for stage, _, _ in PIPELINE_STAGES}
    for row in rows:
        by_stage.setdefault(row["stage"], []).append(row)

    return {
        "queue": {
            key: {"total": len(tab_rows), "rows": tab_rows[:_QUEUE_LIMIT]}
            for key, tab_rows in tabs.items()
        },
        # 목록과 같은 표를 그리므로 유형 이름도 같은 곳에서 옵니다.
        "category_labels": queue["category_labels"],
        "unqualified": queue["unqualified"],
        # The shared queue table dates the 우선순위 dot against "now".
        "now": queue["now"],
        "stages": [
            {
                "key": stage,
                "label": label,
                "rows": by_stage.get(stage, []),
                # What the column HAS, not what it drew.
                "total": stage_totals.get(stage, 0),
            }
            for stage, label, _ in PIPELINE_STAGES
        ],
        "stage_labels": queue["stage_labels"],
        # Which columns offer the 소통 히스토리 (+) button — from 답변 발송 onward, where the
        # thread has left HubSpot and only the operator knows what was said.
        "manual_log_stages": MANUAL_LOG_STAGES,
    }
