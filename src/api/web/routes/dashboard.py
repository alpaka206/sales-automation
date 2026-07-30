"""Dashboard route — the awaiting-reply queue, its counters, and the pipeline board.

The board used to be its own page at /pipeline. It lives here now, below the queue,
because both answer "what needs me next?" and the operator was navigating between them
constantly. /pipeline's POST actions kept their paths; only the page moved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from ....db.models import Conversation, Message
from ....db.session import SessionLocal
from ._shared import templates
from .messages import LIST_STATUS_BUCKETS, _messages_list_context

router = APIRouter(tags=["web"])

# Statuses that mean "a human still has to act on this reply". Not a second copy of the
# 발송대기 bucket on /messages — literally that bucket, so the counters here can never
# count a different set of rows than the list they link to.
AWAITING_STATUSES = LIST_STATUS_BUCKETS["awaiting"]

# Board stages the dashboard counts separately. Everything else is folded into ALL.
_COUNTED_STAGES = ("new", "negotiation")

# The queue panel is a peek, not a list: the five rows that have waited longest. The
# full, filterable list is one click away on 답변 검토, so a longer table here only
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
    from .customer_ops import PIPELINE_STAGES, VALID_PIPELINE_STAGES, _pipeline_rows

    # The queue panel IS the 답변 검토 list — same query, same row shape, same table
    # partial — sorted oldest-first and cut to _QUEUE_LIMIT. Building it here from a
    # second, near-identical query is what let the two tables drift apart before.
    queue = _messages_list_context(status="awaiting", stage="", sort="oldest")
    recent_messages = queue["messages"][:_QUEUE_LIMIT]

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
            awaiting_total += count
            key = stage if stage in VALID_PIPELINE_STAGES else "new"
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

    rows = _pipeline_rows()
    by_stage: dict[str, list] = {stage: [] for stage, _, _ in PIPELINE_STAGES}
    for row in rows:
        by_stage.setdefault(row["stage"], []).append(row)

    return {
        "recent_messages": recent_messages,
        # The shared queue table dates the 우선순위 dot against "now".
        "now": queue["now"],
        "awaiting_total": awaiting_total,
        "awaiting_new": awaiting_by_stage["new"],
        "awaiting_negotiation": awaiting_by_stage["negotiation"],
        "received_today": received_today,
        "stages": [
            {
                "key": stage,
                "label": label,
                "rows": by_stage.get(stage, []),
            }
            for stage, label, _ in PIPELINE_STAGES
        ],
        "stage_labels": queue["stage_labels"],
    }


@router.get("/")
async def dashboard(request: Request):
    """Main inbound dashboard — awaiting replies on top, the pipeline board below."""
    ctx = _dashboard_context()
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@router.get("/overview")
async def overview(request: Request):
    """전체 대시보드 — the whole-business view, first entry in the sidebar.

    A slot, not a page yet: the nav entry exists so the map is settled while what belongs
    on it is decided, the same way 견적서 / 계약서 do. Its path is registered in
    security.WEB_UI_PREFIXES, without which the auth middleware treats it as a JSON API
    route and demands an internal token.
    """
    return templates.TemplateResponse(
        request,
        "tool_placeholder.html",
        {"tool_title": "전체 대시보드", "tool_message": "전체 대시보드는 준비 중입니다."},
    )
