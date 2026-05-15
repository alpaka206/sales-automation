"""Web UI routes — serves Jinja2 templates for the operator dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from ...agents.approval import ApprovalError, approve, reject
from ...common.config import settings
from ...db.models import Contact, Conversation, KnowledgeDocument, Message, Prospect
from ...db.session import SessionLocal
from ...llm.knowledge import reset_cache as _reset_kb_cache

_TEMPLATE_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
router = APIRouter(tags=["web"])

_TRACKED_STATUSES = ("pending_approval", "approved", "sent", "bounced", "replied")


def _dashboard_context() -> dict:
    """Query DB for dashboard data."""
    with SessionLocal() as session:
        recent = (
            session.execute(
                select(Message, Conversation.topic)
                .join(Conversation, Message.conversation_id == Conversation.id)
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
        status_counts = {s: 0 for s in _TRACKED_STATUSES}
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


def _message_detail_context(message_id: int) -> dict:
    """Load a single message with related contact/prospect data."""
    with SessionLocal() as session:
        msg = session.execute(
            select(Message)
            .options(
                joinedload(Message.conversation).joinedload(Conversation.contact),
                joinedload(Message.conversation).joinedload(Conversation.prospect),
            )
            .where(Message.id == message_id)
        ).unique().scalar_one_or_none()
        if not msg:
            return {}

        conv = msg.conversation
        contact = conv.contact if conv else None
        prospect = conv.prospect if conv else None

        return {
            "msg": {
                "id": msg.id,
                "status": msg.status,
                "subject": msg.subject or "",
                "body": msg.body,
                "channel": msg.channel,
                "direction": msg.direction,
                "language": msg.language,
                "to_address": msg.to_address or "",
                "from_address": msg.from_address or "",
                "score_snapshot": msg.score_snapshot,
                "scheduled_at": msg.scheduled_at,
                "sent_at": msg.sent_at,
                "created_at": msg.created_at,
                "category": conv.topic if conv else "-",
            },
            "contact": {
                "id": contact.id,
                "name": contact.full_name,
                "email": contact.email,
                "company": contact.company,
            } if contact else None,
            "prospect": {
                "id": prospect.id,
                "name": prospect.full_name,
                "email": prospect.email,
                "company": prospect.company,
                "icp_score": prospect.icp_score,
            } if prospect else None,
        }


@router.get("/messages/{message_id}")
async def message_detail(request: Request, message_id: int):
    """Message detail page with editable body and send/reject actions."""
    ctx = _message_detail_context(message_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="메시지를 찾을 수 없습니다")
    return templates.TemplateResponse(request, "message_detail.html", ctx)


@router.post("/messages/{message_id}/send")
async def message_send(message_id: int, body: str = Form(""), subject: str = Form("")):
    """Approve (and optionally edit) a message for sending."""
    try:
        edited = body.strip() if body.strip() else None
        approve(message_id, approver="web_ui", edited_body=edited)
    except ApprovalError as exc:
        return HTMLResponse(
            f'<div class="text-red-600 text-sm">{exc}</div>', status_code=400
        )
    return HTMLResponse(
        '<div class="text-green-600 text-sm font-medium">승인 완료 — 발송 대기 중</div>'
    )


@router.post("/messages/{message_id}/reject")
async def message_reject(message_id: int, reason: str = Form("")):
    """Reject a message with an optional reason."""
    try:
        reject(message_id, approver="web_ui", reason=reason.strip() or None)
    except ApprovalError as exc:
        return HTMLResponse(
            f'<div class="text-red-600 text-sm">{exc}</div>', status_code=400
        )
    return HTMLResponse(
        '<div class="text-orange-600 text-sm font-medium">거절 처리 완료</div>'
    )


@router.post("/messages/{message_id}/edit")
async def message_edit(message_id: int, body: str = Form(""), subject: str = Form("")):
    """Save edits to a pending message without sending."""
    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        if not msg:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">메시지를 찾을 수 없습니다</div>',
                status_code=404,
            )
        if msg.status != "pending_approval":
            return HTMLResponse(
                f'<div class="text-red-600 text-sm">편집 불가 (현재 상태: {msg.status})</div>',
                status_code=400,
            )
        if body.strip():
            msg.body = body.strip()
        if subject.strip():
            msg.subject = subject.strip()
        session.commit()
    return HTMLResponse(
        '<div class="text-blue-600 text-sm font-medium">저장 완료</div>'
    )


# ---------- Knowledge base CRUD ----------


def _slugify(title: str) -> str:
    """Simple slug from Korean/English title."""
    import re
    slug = title.lower().strip().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9가-힣\-]", "", slug)
    return slug or "untitled"


@router.get("/knowledge")
async def knowledge_list(request: Request):
    """List all knowledge base documents."""
    with SessionLocal() as session:
        docs = (
            session.query(KnowledgeDocument)
            .order_by(KnowledgeDocument.updated_at.desc())
            .all()
        )
        items = [
            {
                "id": d.id,
                "title": d.title,
                "slug": d.slug,
                "categories": d.categories or [],
                "scope": d.scope,
                "updated_at": d.updated_at,
            }
            for d in docs
        ]
    return templates.TemplateResponse(request, "knowledge_list.html", {"docs": items})


@router.get("/knowledge/new")
async def knowledge_new(request: Request):
    """Form to create a new knowledge document."""
    return templates.TemplateResponse(request, "knowledge_form.html", {
        "doc": None, "mode": "create",
    })


@router.get("/knowledge/{doc_id}")
async def knowledge_edit(request: Request, doc_id: int):
    """Edit form for an existing knowledge document."""
    with SessionLocal() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")
        item = {
            "id": doc.id,
            "title": doc.title,
            "slug": doc.slug,
            "categories": ",".join(doc.categories) if doc.categories else "",
            "scope": doc.scope,
            "body": doc.body,
        }
    return templates.TemplateResponse(request, "knowledge_form.html", {
        "doc": item, "mode": "edit",
    })


@router.post("/knowledge")
async def knowledge_create(
    title: str = Form(""),
    categories: str = Form(""),
    scope: str = Form("both"),
    body: str = Form(""),
):
    """Create a new knowledge document."""
    if not title.strip() or not body.strip():
        return HTMLResponse(
            '<div class="text-red-600 text-sm">제목과 본문은 필수입니다</div>',
            status_code=400,
        )
    cats = [c.strip() for c in categories.split(",") if c.strip()] or None
    slug = _slugify(title.strip())
    with SessionLocal() as session:
        existing = session.query(KnowledgeDocument).filter_by(slug=slug).first()
        if existing:
            slug = f"{slug}-{existing.id + 1}"
        doc = KnowledgeDocument(
            title=title.strip(), slug=slug, categories=cats,
            scope=scope, body=body.strip(),
        )
        session.add(doc)
        session.commit()
    _reset_kb_cache()
    return HTMLResponse(
        '<div class="text-green-600 text-sm font-medium">문서 생성 완료</div>'
        '<script>setTimeout(()=>location.href="/knowledge",500)</script>'
    )


@router.put("/knowledge/{doc_id}")
async def knowledge_update(
    doc_id: int,
    title: str = Form(""),
    categories: str = Form(""),
    scope: str = Form("both"),
    body: str = Form(""),
):
    """Update an existing knowledge document."""
    cats = [c.strip() for c in categories.split(",") if c.strip()] or None
    with SessionLocal() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        if not doc:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">문서를 찾을 수 없습니다</div>',
                status_code=404,
            )
        if title.strip():
            doc.title = title.strip()
        doc.categories = cats
        doc.scope = scope
        if body.strip():
            doc.body = body.strip()
        session.commit()
    _reset_kb_cache()
    return HTMLResponse(
        '<div class="text-green-600 text-sm font-medium">저장 완료</div>'
    )


@router.delete("/knowledge/{doc_id}")
async def knowledge_delete(doc_id: int):
    """Delete a knowledge document."""
    with SessionLocal() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        if not doc:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">문서를 찾을 수 없습니다</div>',
                status_code=404,
            )
        session.delete(doc)
        session.commit()
    _reset_kb_cache()
    return HTMLResponse(
        '<div class="text-orange-600 text-sm font-medium">삭제 완료</div>'
        '<script>setTimeout(()=>location.href="/knowledge",500)</script>'
    )
