"""Dashboard route — recent messages, status counts, daily stats."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from ....common.config import settings
from ....db.models import Conversation, Message
from ....db.session import SessionLocal
from ._shared import TRACKED_STATUSES, templates

router = APIRouter(tags=["web"])


def _dashboard_context() -> dict:
    """Query DB for dashboard data."""
    with SessionLocal() as session:
        # Mirror /messages — show outbound drafts/sent only. Inbound rows are kept
        # for the detail-page reply context but listing them here duplicates the
        # inbound-body box that already appears on each message detail.
        recent = (
            session.execute(
                select(Message, Conversation.topic)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(Message.direction == "outbound")
                .order_by(Message.created_at.desc())
                .limit(20)
            )
            .all()
        )
        recent_messages = [
            {
                "id": msg.id,
                "status": msg.status,
                "category": topic or "-",
                "subject": msg.subject or "(제목 없음)",
                "channel": msg.channel,
                "direction": msg.direction,
                "created_at": msg.created_at,
            }
            for msg, topic in recent
        ]

        status_rows = session.execute(
            select(Message.status, func.count()).group_by(Message.status)
        ).all()
        status_counts = {s: 0 for s in TRACKED_STATUSES}
        for status, cnt in status_rows:
            status_counts[status] = cnt

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_sent = session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.status == "sent", Message.sent_at >= today_start)
        ) or 0

        category_rows = session.execute(
            select(Conversation.topic, func.count())
            .join(Message, Conversation.id == Message.conversation_id)
            .where(Conversation.topic.isnot(None))
            .group_by(Conversation.topic)
            .order_by(func.count().desc())
        ).all()
        category_counts = [(cat or "기타", cnt) for cat, cnt in category_rows]

    return {
        "recent_messages": recent_messages,
        "status_counts": status_counts,
        "today_sent": today_sent,
        "daily_limit": settings.DAILY_SEND_LIMIT,
        "category_counts": category_counts,
    }


@router.get("/")
async def dashboard(request: Request):
    """Main dashboard — recent messages, status counts, daily stats."""
    ctx = _dashboard_context()
    return templates.TemplateResponse(request, "dashboard.html", ctx)
