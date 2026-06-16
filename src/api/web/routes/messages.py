"""Message list, detail, and approval-action (send/reject/edit) routes."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from ....agents.approval import ApprovalError, approve, reject
from ....common.config import settings
from ....db.models import Conversation, DomainProfile, Message
from ....db.session import SessionLocal
from ....llm.translate import needs_korean, to_korean
from ..auth import actor_name
from ._shared import esc, templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])

# Maximum bytes accepted for a single edit — prevents accidental/malicious DoS via huge POST.
_MAX_EDIT_BODY_BYTES = 100_000
_MAX_EDIT_SUBJECT_LEN = 300


def _message_detail_context(message_id: int) -> dict:
    """Load a single message with related contact/prospect data."""
    with SessionLocal() as session:
        msg = (
            session.execute(
                select(Message)
                .options(
                    joinedload(Message.conversation).joinedload(Conversation.contact),
                    joinedload(Message.conversation).joinedload(Conversation.prospect),
                )
                .where(Message.id == message_id)
            )
            .unique()
            .scalar_one_or_none()
        )
        if not msg:
            return {}

        conv = msg.conversation
        contact = conv.contact if conv else None
        prospect = conv.prospect if conv else None

        # Pull the inbound messages from the same conversation so the approver can see
        # what they're replying to. Newest first, capped at 5 — UI shows them above the draft.
        inbound_rows = []
        thread_rows = []
        if conv:
            inbound_rows = (
                session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conv.id,
                        Message.direction == "inbound",
                    )
                    .order_by(Message.created_at.desc())
                    .limit(5)
                )
                .scalars()
                .all()
            )

            # Full ticket/conversation thread, oldest → newest. One ticket = one
            # conversation (see agents/inbound.py), so this is the complete back-and-forth
            # history for this ticket: every inbound inquiry and every outbound reply/follow-up.
            thread_rows = (
                session.execute(
                    select(Message)
                    .where(Message.conversation_id == conv.id)
                    .order_by(Message.created_at.asc(), Message.id.asc())
                )
                .scalars()
                .all()
            )

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
                    "subject": im.subject,
                    "from_address": im.from_address,
                    "channel": im.channel,
                    "created_at": im.created_at,
                }
                for im in inbound_rows
            ],
            # Whole-ticket timeline: inquiries and replies interleaved in chronological
            # order. The current message (being approved/viewed) is flagged so the
            # template can render it as the editable reply card inline in the thread.
            "thread": [
                {
                    "id": tm.id,
                    "direction": tm.direction,
                    "status": tm.status,
                    "subject": tm.subject,
                    "body": tm.body,
                    # Cheap heuristic only — the actual Korean translation is loaded
                    # lazily via /messages/{id}/translation so the page renders fast.
                    "translatable": needs_korean(tm.body),
                    "channel": tm.channel,
                    "from_address": tm.from_address,
                    "to_address": tm.to_address,
                    "created_at": tm.created_at,
                    "sent_at": tm.sent_at,
                    "is_current": tm.id == msg.id,
                }
                for tm in thread_rows
            ],
            "ticket": {
                "ticket_id": conv.hubspot_ticket_id if conv else None,
                "stage": conv.stage if conv else None,
                "topic": conv.topic if conv else None,
            },
            "msg": {
                "id": msg.id,
                "status": msg.status,
                "subject": msg.subject or "",
                "body": msg.body,
                "translatable": needs_korean(msg.body),
                "channel": msg.channel,
                "direction": msg.direction,
                # Product flow, not raw DB direction. A reply we draft to an inbound
                # inquiry is direction="outbound" but is conceptually an inbound reply;
                # outbound-discovery conversations carry a prospect.
                "flow": "outbound" if prospect is not None else "inbound_reply",
                "language": msg.language,
                "to_address": msg.to_address or "",
                "from_address": msg.from_address or "",
                "score_snapshot": msg.score_snapshot,
                "scheduled_at": msg.scheduled_at,
                "sent_at": msg.sent_at,
                "created_at": msg.created_at,
                "category": conv.topic if conv else "-",
            },
            "contact": (
                {
                    "id": contact.id,
                    "name": contact.full_name,
                    "email": contact.email,
                    "company": contact.company,
                }
                if contact
                else None
            ),
            "prospect": (
                {
                    "id": prospect.id,
                    "name": prospect.full_name,
                    "email": prospect.email,
                    "company": prospect.company,
                    "icp_score": prospect.icp_score,
                }
                if prospect
                else None
            ),
            "domain_profile": domain_profile_data,
        }


def _messages_list_context(status: str = "", channel: str = "") -> dict:
    """Query DB for paginated message list.

    The list is the approval queue — outbound drafts and sent replies only. Inbound
    rows are persisted (so the detail page can show "what we're replying to") but
    rendering them here would duplicate the box at the top of the detail page.
    """
    q = (
        select(Message, Conversation.topic, Conversation.prospect_id)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Message.direction == "outbound")
        .order_by(Message.created_at.desc())
    )
    if status == "replied":
        # Replies are tracked on the boolean Message.replied column (set by
        # reply_check), not as a status — a replied message keeps status="sent".
        # Mirrors the dashboard "누적 응답" metric.
        q = q.where(Message.replied.is_(True))
    elif status:
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
                # Product flow, not DB direction: reply to an inbound inquiry vs outbound cold mail.
                "flow": "outbound" if prospect_id is not None else "inbound_reply",
                "to_address": msg.to_address or "-",
                "created_at": msg.created_at,
            }
            for msg, topic, prospect_id in rows
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


@router.get("/messages/{message_id}/translation")
async def message_translation(message_id: int):
    """Lazily translate every non-Korean bubble in the thread to Korean.

    Returns htmx out-of-band fragments that fill the empty `#ko-<id>` placeholders
    rendered by the detail page. Kept off the initial page load (it makes one LLM
    call per bubble) so opening a message is fast; the operator only pays the
    translation cost when they click "번역으로 보기". Calls run concurrently.
    """
    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        if not msg:
            return HTMLResponse("", status_code=404)
        rows = (
            session.execute(
                select(Message)
                .where(Message.conversation_id == msg.conversation_id)
                .order_by(Message.created_at.asc(), Message.id.asc())
            )
            .scalars()
            .all()
        )
        targets = [(m.id, m.body) for m in rows if needs_korean(m.body)]

    async def _translate(mid: int, body: str) -> tuple[int, str]:
        return mid, await asyncio.to_thread(to_korean, body)

    results = await asyncio.gather(*(_translate(mid, body) for mid, body in targets))

    frags = [
        f'<div id="ko-{mid}" hx-swap-oob="innerHTML">{esc(ko) or "(번역을 가져오지 못했습니다)"}</div>'
        for mid, ko in results
    ]
    return HTMLResponse("".join(frags))


@router.post("/messages/{message_id}/send")
async def message_send(
    request: Request, message_id: int, body: str = Form(""), subject: str = Form("")
):
    """Approve (and optionally edit) a message, then send it immediately.

    Human approval IS the decision to send, so we dispatch inline here rather than
    leaving the message in 'approved' for the background send worker — a paused or
    absent worker must never strand an already-approved reply.
    """
    try:
        edited = body.strip() if body.strip() else None
        approve(message_id, approver=actor_name(request, fallback="web_ui"), edited_body=edited)
    except ApprovalError as exc:
        return HTMLResponse(
            f'<div class="text-red-600 text-sm">{esc(str(exc))}</div>', status_code=400
        )

    from ....agents.approval import mark_sent
    from ....integrations.senders import send

    subj = bod = contact_id = ""
    try:
        # Send with a session-attached message so send() can read its conversation
        # (the instance returned by approve() is detached).
        with SessionLocal() as session:
            m = session.get(Message, message_id)
            if m is None:
                return HTMLResponse(
                    '<div class="text-red-600 text-sm">메시지를 찾을 수 없습니다</div>',
                    status_code=404,
                )
            await send(m)
            subj, bod = m.subject or "", m.body or ""
            conv = m.conversation
            contact_id = str(conv.contact_id) if conv and conv.contact_id else ""
        mark_sent(message_id)
    except Exception:
        logger.exception("Inline send failed for message %d after approval", message_id)
        return HTMLResponse(
            '<div class="text-red-600 text-sm font-medium">승인됐지만 발송에 실패했습니다 — 잠시 후 다시 시도해 주세요</div>',
            status_code=500,
        )

    # Best-effort HubSpot timeline log when send() didn't already (SMTP / test mode).
    # Never reverses a successful send.
    if contact_id and (settings.SEND_OVERRIDE_EMAIL.strip() or settings.EMAIL_PROVIDER == "smtp"):
        try:
            from ....integrations.hubspot import HubSpotClient

            await HubSpotClient().create_email_engagement(
                contact_id=contact_id, subject=subj, body=bod
            )
        except Exception:
            logger.warning(
                "HubSpot engagement log failed for message %d (send succeeded)",
                message_id,
                exc_info=True,
            )

    return HTMLResponse('<div class="text-green-600 text-sm font-medium">승인 및 발송 완료</div>')


@router.post("/messages/preview")
async def message_preview(body: str = Form("")):
    """Render a draft body as the HTML email it will become — live approval preview.

    Stateless: takes the (possibly edited) textarea content and returns the same
    styled HTML that the send path attaches, so the approver sees the real look.
    """
    from ....integrations.email_html import to_html_email

    return HTMLResponse(to_html_email(body))


@router.post("/messages/{message_id}/reject")
async def message_reject(request: Request, message_id: int, reason: str = Form("")):
    """Reject a message with an optional reason."""
    try:
        reject(
            message_id,
            approver=actor_name(request, fallback="web_ui"),
            reason=reason.strip() or None,
        )
    except ApprovalError as exc:
        return HTMLResponse(
            f'<div class="text-red-600 text-sm">{esc(str(exc))}</div>', status_code=400
        )
    return HTMLResponse('<div class="text-orange-600 text-sm font-medium">거절 처리 완료</div>')


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
    return HTMLResponse('<div class="text-blue-600 text-sm font-medium">저장 완료</div>')
