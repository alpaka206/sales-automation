"""Dashboard route — the awaiting-reply queue, its counters, and the pipeline board.

The board used to be its own page at /pipeline. It lives here now, below the queue,
because both answer "what needs me next?" and the operator was navigating between them
constantly. /pipeline's POST actions kept their paths; only the page moved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import func, select

from ...db.models import Message
from ...db.session import SessionLocal
from .messages import _messages_list_context

router = APIRouter(tags=["web"])

# The queue panel is a peek, not a list: the five rows that have waited longest. The
# full, filterable list is one click away on 회신 및 검토, so a longer table here only
# pushed the pipeline board off the screen.
_QUEUE_LIMIT = 5


def _kst_day_start() -> datetime:
    """Midnight in KST, expressed as the naive UTC the DB columns store.

    The counter is labelled 오늘 and the UI renders KST, so a UTC midnight would move
    the boundary by nine hours — inquiries from 09:00 KST onward counted as yesterday.
    """
    kst_now = datetime.now(timezone.utc) + timedelta(hours=9)
    kst_midnight = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (kst_midnight - timedelta(hours=9)).replace(tzinfo=None)


def _dashboard_context() -> dict:
    """Awaiting-reply rows, their counters, and the pipeline board."""
    from .customer_ops import (
        MANUAL_LOG_STAGES,
        PIPELINE_STAGES,
        _pipeline_rows,
    )

    # The queue panel IS the 회신 및 검토 list — same query, same row shape, same table
    # partial — sorted oldest-first and cut to _QUEUE_LIMIT. Building it here from a
    # second, near-identical query is what let the two tables drift apart before.
    queue = _messages_list_context(status="awaiting", stage="", sort="oldest")
    recent_messages = queue["messages"][:_QUEUE_LIMIT]
    # **「답변 대기」 숫자는 이 목록의 총계 그대로다** — 자르기 전의 행 수(`total`). 숫자를 따로 세던 쿼리가 두 번
    # 목록과 다른 말을 했다(2026-08-05 의 6 대 1, 2026-08-31 에 목록에만 붙은 수동 초안 예외) — 화면이 여는 목록과
    # 어긋난 숫자는 없는 일감을 찾게 만든다.
    awaiting_total = queue["total"]
    with SessionLocal() as session:
        received_today = session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.direction == "inbound", Message.created_at >= _kst_day_start())
        ) or 0

    # Capped per column, with the true per-stage totals alongside — the header must not
    # start under-reporting just because the column stopped rendering every card.
    rows, stage_totals = _pipeline_rows()
    by_stage: dict[str, list] = {stage: [] for stage, _, _ in PIPELINE_STAGES}
    for row in rows:
        by_stage.setdefault(row["stage"], []).append(row)

    return {
        "recent_messages": recent_messages,
        # 목록과 같은 표를 그리므로 유형 이름도 같은 곳에서 옵니다.
        "category_labels": queue["category_labels"],
        "unqualified": queue["unqualified"],
        # The shared queue table dates the 우선순위 dot against "now".
        "now": queue["now"],
        # 단계별 수는 헤더에 두지 않습니다 — 바로 아래 보드의 각 열 머리에 그대로 있습니다.
        "awaiting_total": awaiting_total,
        "received_today": received_today,
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
