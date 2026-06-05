"""Message list, detail, and approval-action (send/reject/edit) routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from ....agents.approval import ApprovalError, approve, reject
from ....db.models import Conversation, DomainProfile, Message
from ....db.session import SessionLocal
from ....llm.translate import needs_korean, to_korean
from ._shared import esc, templates

router = APIRouter(tags=["web"])

# Maximum bytes accepted for a single edit — prevents accidental/malicious DoS via huge POST.
_MAX_EDIT_BODY_BYTES = 100_000
_MAX_EDIT_SUBJECT_LEN = 300


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

        # Pull the inbound messages from the same conversation so the approver can see
        # what they're replying to. Newest first, capped at 5 — UI shows them above the draft.
        inbound_rows = []
        if conv:
            inbound_rows = session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conv.id,
                    Message.direction == "inbound",
                )
                .order_by(Message.created_at.desc())
                .limit(5)
            ).scalars().all()

        domain_profile_data = None
        if contact and contact.domain:
            dp = session.get(DomainProfile, contact.domain)
            if dp:
                domain_profile_data = {
                    "domain": dp.domain,
                    "company_name": dp.company_name,
                    "industry": dp.industry,
                    "services": dp.services,
                    "target_market": dp.target_market,
                    "size_hint": dp.size_hint,
                    "confidence": dp.confidence,
                    "source": dp.source,
                    "analyzed_at": dp.analyzed_at,
                }

        return {
            "inbound_messages": [
                {
                    "id": im.id,
                    "body": im.body,
                    "body_ko": to_korean(im.body) if needs_korean(im.body) else None,
                    "subject": im.subject,
                    "from_address": im.from_address,
                    "channel": im.channel,
                    "created_at": im.created_at,
                }
                for im in inbound_rows
            ],
            "msg": {
                "id": msg.id,
                "status": msg.status,
                "subject": msg.subject or "",
                "body": msg.body,
                "body_ko": to_korean(msg.body) if needs_korean(msg.body) else None,
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
            "domain_profile": domain_profile_data,
        }


def _messages_list_context(status: str = "", channel: str = "") -> dict:
    """Query DB for paginated message list.

    The list is the approval queue — outbound drafts and sent replies only. Inbound
    rows are persisted (so the detail page can show "what we're replying to") but
    rendering them here would duplicate the box at the top of the detail page.
    """
    q = (
        select(Message, Conversation.topic)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Message.direction == "outbound")
        .order_by(Message.created_at.desc())
    )
    if status:
        q = q.where(Message.status == status)
    if channel:
        q = q.where(Message.channel == channel)
    q = q.limit(100)
    with SessionLocal() as session:
        rows = session.execute(q).all()
        messages = [
            {
                "id": msg.id,
                "status": msg.status,
                "category": topic or "-",
                "subject": msg.subject or "(제목 없음)",
                "channel": msg.channel,
                "direction": msg.direction,
                "to_address": msg.to_address or "-",
                "created_at": msg.created_at,
            }
            for msg, topic in rows
        ]
    return {"messages": messages, "filter_status": status, "filter_channel": channel}


@router.get("/messages")
async def messages_list(request: Request):
    """Message list page — all messages with optional status/channel filters."""
    status = request.query_params.get("status", "")
    channel = request.query_params.get("channel", "")
    ctx = _messages_list_context(status=status, channel=channel)
    return templates.TemplateResponse(request, "messages_list.html", ctx)


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
            f'<div class="text-red-600 text-sm">{esc(str(exc))}</div>', status_code=400
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
            f'<div class="text-red-600 text-sm">{esc(str(exc))}</div>', status_code=400
        )
    return HTMLResponse(
        '<div class="text-orange-600 text-sm font-medium">거절 처리 완료</div>'
    )


@router.post("/messages/{message_id}/edit")
async def message_edit(message_id: int, body: str = Form(""), subject: str = Form("")):
    """Save edits to a pending message without sending."""
    if len(body.encode("utf-8")) > _MAX_EDIT_BODY_BYTES:
        return HTMLResponse(
            '<div class="text-red-600 text-sm">본문이 너무 깁니다 (100KB 초과)</div>',
            status_code=413,
        )
    if len(subject) > _MAX_EDIT_SUBJECT_LEN:
        return HTMLResponse(
            '<div class="text-red-600 text-sm">제목이 너무 깁니다 (300자 초과)</div>',
            status_code=413,
        )
    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        if not msg:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">메시지를 찾을 수 없습니다</div>',
                status_code=404,
            )
        if msg.status != "pending_approval":
            return HTMLResponse(
                f'<div class="text-red-600 text-sm">편집 불가 (현재 상태: {esc(msg.status)})</div>',
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
