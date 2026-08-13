"""Dashboard route — the awaiting-reply queue, its counters, and the pipeline board.

The board used to be its own page at /pipeline. It lives here now, below the queue,
because both answer "what needs me next?" and the operator was navigating between them
constantly. /pipeline's POST actions kept their paths; only the page moved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import func, select

from ...db.models import Conversation, Message
from ...db.session import SessionLocal
from .messages import LIST_STATUS_BUCKETS, _messages_list_context

router = APIRouter(tags=["web"])

# Statuses that mean "a human still has to act on this reply". Not a second copy of the
# 발송대기 bucket on /messages — literally that bucket, so the counters here can never
# count a different set of rows than the list they link to.
AWAITING_STATUSES = LIST_STATUS_BUCKETS["awaiting"]

# Board stages the dashboard counts separately. Everything else is folded into ALL.
_COUNTED_STAGES = ("new", "negotiation")

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


def _awaiting_counters() -> dict:
    """오늘 받은 문의 and what is waiting on a human, by stage.

    Its own function because 전체 대시보드 shows the same numbers as 문의 대시보드.
    Two screens counting "검토 대기" with two queries is how they end up disagreeing.

    ``awaiting_total`` counts **only the stages 회신 및 검토 actually lists.** It used to
    sum every stage, so the dashboard said 6 while the list it links to held 1 — the five
    others were drafts on tickets somebody had already answered in HubSpot, which that
    list stopped showing. A counter that disagrees with the screen it opens is worse than
    no counter: it sends the operator looking for work that is not there.
    """
    from .customer_ops import VALID_PIPELINE_STAGES
    from .messages import LIST_STAGES

    listed = set(LIST_STAGES["awaiting"])

    with SessionLocal() as session:
        stage_rows = session.execute(
            select(Conversation.stage, func.count())
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Message.direction == "outgoing")
            .where(Message.status.in_(AWAITING_STATUSES))
            .where((Message.prompt_variant.is_(None)) | (Message.prompt_variant != "auto_ack"))
            .group_by(Conversation.stage)
        ).all()
        awaiting_by_stage = {stage: 0 for stage in _COUNTED_STAGES}
        awaiting_total = 0
        for stage, count in stage_rows:
            key = stage if stage in VALID_PIPELINE_STAGES else "new"
            if key in listed:
                awaiting_total += count
            if key in awaiting_by_stage:
                awaiting_by_stage[key] += count

        received_today = (
            session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.direction == "inbound", Message.created_at >= _kst_day_start())
            )
            or 0
        )
    return {
        "received_today": received_today,
        "awaiting_total": awaiting_total,
        "awaiting_by_stage": awaiting_by_stage,
    }



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

    counters = _awaiting_counters()
    awaiting_total = counters["awaiting_total"]
    received_today = counters["received_today"]

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
        # New/Negotiating 는 헤더에서 뺐습니다: 발송 대기가 New 만 보여주게 된 뒤로 ALL 과
        # New 가 같은 수를 세게 되었고, 같은 수를 두 번 적는 칸이었습니다. 단계별 수는 바로
        # 아래 보드의 각 열 머리에 그대로 있습니다.
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
        # Which columns offer the 소통 기록 (+) button — from 답변 발송 onward, where the
        # thread has left HubSpot and only the operator knows what was said.
        "manual_log_stages": MANUAL_LOG_STAGES,
    }
