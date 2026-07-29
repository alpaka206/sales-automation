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

router = APIRouter(tags=["web"])

# Statuses that mean "a human still has to act on this reply". Mirrors the 발송대기
# chip on /messages (routes/messages.py) — the dashboard is that list, truncated.
AWAITING_STATUSES = ("pending_approval", "drafting", "draft_failed", "send_failed")

# Board stages the dashboard counts separately. Everything else is folded into ALL.
_COUNTED_STAGES = ("new", "negotiation")

_RECENT_LIMIT = 8


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

    with SessionLocal() as session:
        awaiting = (
            select(Message, Conversation.stage, Conversation.inquiry_subject)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Message.direction == "outgoing")
            .where(Message.status.in_(AWAITING_STATUSES))
            .where((Message.prompt_variant.is_(None)) | (Message.prompt_variant != "auto_ack"))
        )
        recent = session.execute(
            awaiting.order_by(Message.created_at.desc()).limit(_RECENT_LIMIT)
        ).all()
        recent_messages = [
            {
                "id": msg.id,
                "status": msg.status,
                # Coerced the same way the board does it, so a legacy "initial" row
                # does not render a stage the operator has never seen.
                "stage": stage if stage in VALID_PIPELINE_STAGES else "new",
                "subject": inquiry_subject or msg.subject or "(제목 없음)",
                "channel": msg.channel,
                "created_at": msg.created_at,
            }
            for msg, stage, inquiry_subject in recent
        ]

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
        "awaiting_total": awaiting_total,
        "awaiting_new": awaiting_by_stage["new"],
        "awaiting_negotiation": awaiting_by_stage["negotiation"],
        "received_today": received_today,
        "stages": [
            {
                "key": stage,
                "label": label,
                "description": description,
                "rows": by_stage.get(stage, []),
            }
            for stage, label, description in PIPELINE_STAGES
        ],
        "stage_options": PIPELINE_STAGES,
        "stage_labels": {key: label for key, label, _ in PIPELINE_STAGES},
    }


@router.get("/")
async def dashboard(request: Request):
    """Main inbound dashboard — awaiting replies on top, the pipeline board below."""
    ctx = _dashboard_context()
    return templates.TemplateResponse(request, "dashboard.html", ctx)
