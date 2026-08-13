"""Message list, detail, and approval-action (send/reject/edit) routes."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from ...agents.approval import ApprovalError, approve, reject
from ...common.config import settings
from ...common.subjects import strip_reply_prefixes
from ...common.textwash import text_wash
from ...db.conversation_history import add_progress
from ...common.inquiry import CATEGORY_LABELS, UNQUALIFIED, category_label, is_unqualified
from ...db.email_templates import list_signature_templates
from ...db.models import (
    Contact,
    ContractRecord,
    Conversation,
    ConversationProgress,
    CustomerInteraction,
    CustomerProfile,
    DomainProfile,
    Message,
)
from ...db.session import SessionLocal
from ...llm.translate import is_mostly_korean, needs_korean, translate_to
from ..auth import actor_name
from ._shared import esc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])

# Maximum bytes accepted for a single edit — prevents accidental/malicious DoS via huge POST.
def list_now() -> datetime:
    """The clock a list is dated against, truncated to the minute.

    It rides in the payload so every row's "waited N days" is measured against one
    instant instead of each browser's own clock. Truncated because a microsecond-precise
    timestamp changes the response body on every single request, which defeats the
    conditional GET in front of it — the console would re-download an identical list every
    15 seconds forever. The dot it feeds is bucketed in DAYS; a minute of coarseness is
    invisible to it and turns three of four polls into a 304.
    """
    # Rounded UP, not down. Truncating alone puts `now` up to 59 seconds in the past,
    # and a row created moments ago then reports a NEGATIVE age against it.
    return (datetime.now(timezone.utc) + timedelta(minutes=1)).replace(second=0, microsecond=0)


_MAX_EDIT_BODY_BYTES = 100_000

# 처리 경과 answers "what happened with this customer". These answer "what the app did to
# itself" and say nothing the screen is not already showing: the auto-acknowledgement is
# the first row of the thread above, "초안 작성 완료" is what the 검토 대기 status means,
# and "HubSpot에서 단계 변경 감지: new → meeting_link_sent" is bookkeeping about a move
# the Stage column already displays. Hidden on read only — the rows are still written and
# still there to explain a support question. A FAILED auto-ack and a draft HubSpot retired
# out from under the operator are different sentences that need a human, so they keep
# their own kinds and are not in here.
_ROUTINE_PROGRESS_KINDS = ("draft", "auto_ack", "stage")
_MAX_EDIT_SUBJECT_LEN = 300


def _clean_signature_key(value: str | None) -> str | None:
    """The posted signature choice, or None for 서명 없음.

    Anything that is not an active signature — the empty option, a stale ``"none"`` from
    when 기본 텍스트 서명 was a third choice, a forged key — becomes None, so a bad value
    can never select an arbitrary template. There is nothing to distinguish any more:
    "no signature" is what None means now that nothing writes one into the body.
    """
    v = (value or "").strip()
    return v if v in {s["key"] for s in list_signature_templates()} else None


def _message_detail_context(message_id: int) -> dict:
    """Load a single message with its related customer data."""
    from .customer_ops import visible_deal_detail

    with SessionLocal() as session:
        msg = (
            session.execute(
                select(Message)
                .options(
                    joinedload(Message.conversation).joinedload(Conversation.contact),
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

        thread_rows = []
        progress_rows = []
        interaction_rows = []
        if conv:
            # Full ticket/conversation thread, oldest → newest — every inbound inquiry
            # and every outgoing reply or auto-ack for this thread.
            thread_rows = (
                session.execute(
                    select(Message)
                    .where(Message.conversation_id == conv.id)
                    .order_by(Message.created_at.asc(), Message.id.asc())
                )
                .scalars()
                .all()
            )
            # 처리 경과, oldest → newest. Filtered on READ, never deleted: progress rows
            # are append-only, and what the machine did to itself is still worth having
            # in the row when something has to be explained.
            progress_rows = (
                session.execute(
                    select(ConversationProgress)
                    .where(ConversationProgress.conversation_id == conv.id)
                    .where(ConversationProgress.kind.not_in(_ROUTINE_PROGRESS_KINDS))
                    .order_by(ConversationProgress.created_at.asc(), ConversationProgress.id.asc())
                )
                .scalars()
                .all()
            )
            # The other half of the story: after the first reply the thread leaves
            # HubSpot for mail, WhatsApp, a phone call or a meeting, and only the
            # operator knows what was said. Those notes belong on this log, not on a
            # separate one — "메일이 나갔다 → 미팅했고 요구사항은 이것" is one sequence.
            interaction_rows = (
                session.execute(
                    select(CustomerInteraction)
                    .where(CustomerInteraction.conversation_id == conv.id)
                    .order_by(CustomerInteraction.happened_at.asc(), CustomerInteraction.id.asc())
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

        domain_history = (
            _domain_history(session, contact.domain, exclude_conv_id=conv.id if conv else None)
            if (contact and contact.domain)
            else None
        )

        # Customer-level history (CRM state, contract, cross-channel touchpoints)
        # surfaced inline so the operator sees who this customer is without leaving
        # the reply screen. Full editable view stays at /customers/{id}.
        customer = (
            _customer_history(
                session, contact.id, exclude_conversation_id=conv.id if conv else None
            )
            if contact
            else None
        )

        # The customer's inquiry is shown TRANSLATED (Korean) by default with the
        # original behind an expand toggle. ``needs_ko`` flags inbound non-Korean
        # bubbles; ``body_ko``/``subject_ko`` are filled by the route (concurrently)
        # so the page already shows Korean without a click.
        thread = [
            {
                "id": tm.id,
                "direction": tm.direction,
                "status": tm.status,
                "subject": tm.subject,
                "body": tm.body,
                # Translate inbound bubbles when EITHER the body or the subject is
                # non-Korean (a Korean body can still have an English subject line).
                "needs_ko": tm.direction == "inbound"
                and (needs_korean(tm.body) or needs_korean(tm.subject or "")),
                "body_ko": tm.body_ko,
                "subject_ko": tm.subject_ko,
                "is_auto_ack": tm.prompt_variant == "auto_ack",
                "language": tm.language,
                "channel": tm.channel,
                "from_address": tm.from_address,
                "to_address": tm.to_address,
                "created_at": tm.created_at,
                "sent_at": tm.sent_at,
                "is_current": tm.id == msg.id,
            }
            for tm in thread_rows
        ]

        return {
            "thread": thread,
            "progress": sorted(
                [
                    {"kind": p.kind, "detail": p.detail, "created_at": p.created_at,
                     "channel": None, "handler": None}
                    for p in progress_rows
                ]
                + [
                    {"kind": "interaction", "detail": i.summary, "created_at": i.happened_at,
                     "channel": i.channel, "handler": i.handler}
                    for i in interaction_rows
                ],
                key=lambda entry: entry["created_at"],
            ),
            "summary": conv.summary if conv else None,
            "customer_requests": conv.customer_requests if conv else None,
            "category": conv.inquiry_category if conv else None,
            "category_label": category_label(conv.inquiry_category if conv else None),
            "unqualified": is_unqualified(conv.inquiry_category if conv else None),
            "signatures": list_signature_templates(),
            "domain_history": domain_history,
            "ticket": {
                "id": conv.id if conv else None,
                "ticket_id": conv.hubspot_ticket_id if conv else None,
                "stage": conv.stage if conv else None,
                # Won Type / Lost Reason. 보드 카드와 **같은 값·같은 규칙**입니다: 지금
                # 단계의 목록에 없는 값은 안 내려보냅니다(Won 에서 고른 값이 Lost 사유
                # 자리에 붙으면 안 됩니다). 값 자체는 지우지 않으므로 되돌아오면 다시 뜹니다.
                "deal_detail": visible_deal_detail(
                    conv.stage if conv else None, conv.deal_detail if conv else None
                ),
                "inquiry_subject": conv.inquiry_subject if conv else None,
                "inquiry_language": conv.inquiry_language if conv else None,
                # The Inbound DB workbook's stable key for this inquiry (e.g. 1330).
                # Threads this app appended carry it on the conversation; ones imported
                # from the sheet carry it on the contact — same fallback order every
                # stage-sync path uses.
                "client_id": (
                    (conv.sheet_client_id if conv else None)
                    or (contact.sheet_client_id if contact else None)
                ),
            },
            # This ticket's own manual touchpoints — what happened on email, WhatsApp,
            # phone or SMS after the first reply. Contact-wide records (logged from
            # 리드 히스토리, or synced from HubSpot) carry no conversation_id and stay
            # in the sidebar's 접점 기록 instead.
            "ticket_interactions": (
                [
                    {
                        "id": it.id,
                        "channel": it.channel,
                        "direction": it.direction,
                        "handler": it.handler,
                        "subject": it.subject,
                        "summary": it.summary,
                        "context": it.context,
                        "artifact_url": it.artifact_url,
                        "happened_at": it.happened_at,
                    }
                    # 위에서 이미 읽어 둔 같은 행들입니다(오래된 순). 여기서 최신순으로
                    # 한 번 더 SELECT 하고 있었는데, 조건도 같고 세션도 같아 결과가 같습니다
                    # — 뒤집기만 하면 됩니다. 왕복 하나가 200ms 인 데다 이 화면이 제일 자주
                    # 열립니다.
                    for it in reversed(interaction_rows)
                ]
                if conv
                else []
            ),
            "msg": {
                "id": msg.id,
                "status": msg.status,
                "subject": msg.subject or "",
                "body": msg.body,
                "body_ko": None,
                "channel": msg.channel,
                "direction": msg.direction,
                # Every thread starts from an inbound inquiry.
                "flow": "inbound_reply",
                "language": msg.language,
                "target_language": msg.target_language,
                "signature_key": msg.signature_key or "",
                "to_address": msg.to_address or "",
                "from_address": msg.from_address or "",
                "score_snapshot": msg.score_snapshot,
                "scheduled_at": msg.scheduled_at,
                "sent_at": msg.sent_at,
                "created_at": msg.created_at,
            },
            "contact": (
                {
                    "id": contact.id,
                    "name": contact.full_name,
                    "email": contact.email,
                    "company": contact.company,
                    "domain": contact.domain,
                    "role_description": contact.role_description,
                }
                if contact
                else None
            ),
            "domain_profile": domain_profile_data,
            "customer": customer,
        }


def _customer_history(session, contact_id: int, exclude_conversation_id: int | None = None) -> dict:
    """Read-only customer-level history for the message-detail sidebar.

    Mirrors the pieces of the /customers/{id} page that are NOT already on the
    reply screen: the CustomerProfile snapshot (pipeline/state/temperature/next
    action), the latest contract, and the cross-channel touchpoint log
    (CustomerInteraction — manual notes + HubSpot-synced emails/deals/notes).
    Everything is serialized to plain dicts before the session closes, so the
    template never touches a detached ORM object. Editing lives at /customers/{id}.

    ``exclude_conversation_id`` drops the records THIS ticket already lists in its own
    소통 기록 card, the same way ``_domain_history`` excludes the open thread — otherwise
    every call the operator logs here would render twice on one screen.
    """
    profile = session.get(CustomerProfile, contact_id)
    interaction_q = select(CustomerInteraction).where(
        CustomerInteraction.contact_id == contact_id
    )
    if exclude_conversation_id is not None:
        interaction_q = interaction_q.where(
            (CustomerInteraction.conversation_id.is_(None))
            | (CustomerInteraction.conversation_id != exclude_conversation_id)
        )
    interactions = (
        session.execute(
            interaction_q.order_by(CustomerInteraction.happened_at.desc()).limit(6)
        )
        .scalars()
        .all()
    )
    contract = (
        session.execute(
            select(ContractRecord)
            .where(ContractRecord.contact_id == contact_id)
            .order_by(ContractRecord.created_at.desc())
            .limit(1)
        )
        .scalar_one_or_none()
    )

    profile_data = (
        {
            "customer_state": profile.customer_state,
            "pipeline_stage": profile.pipeline_stage,
            "lead_temperature": profile.lead_temperature,
            "current_plan": profile.current_plan,
            "next_action": profile.next_action,
            "next_action_at": profile.next_action_at,
        }
        if profile
        else None
    )
    contract_data = (
        {
            "status": contract.status,
            "plan": contract.plan,
            "amount": contract.amount,
            "currency": contract.currency,
            "expires_at": contract.expires_at,
        }
        if contract
        else None
    )
    interaction_rows = [
        {
            "channel": it.channel,
            "direction": it.direction,
            "handler": it.handler,
            "subject": it.subject,
            "summary": it.summary,
            "happened_at": it.happened_at,
        }
        for it in interactions
    ]
    return {
        "profile": profile_data,
        "contract": contract_data,
        "interactions": interaction_rows,
        "has_any": bool(profile_data or contract_data or interaction_rows),
    }


def _domain_history(session, domain: str, exclude_conv_id: int | None = None) -> dict:
    """All other conversations sharing this email domain (same company).

    Captures both "same person, different ticket" and "different people, same
    company". Returns a summary dict the sidebar renders and the company page links
    to. Personal/free-email domains (gmail, naver, …) are NEVER grouped — that would
    expose one customer's history to an unrelated customer on the same provider.
    """
    from ...common.domains import is_personal_domain

    if not domain or is_personal_domain(domain):
        return {"domain": domain, "total": 0, "rows": []}
    rows = session.execute(
        select(Conversation, Contact)
        .join(Contact, Conversation.contact_id == Contact.id)
        .where(func.lower(Contact.domain) == domain.lower())
        .order_by(Conversation.created_at.desc())
    ).all()
    convs = [(c, ct) for c, ct in rows if c.id != exclude_conv_id]
    if not convs:
        return {"domain": domain, "total": 0, "rows": []}

    conv_ids = [c.id for c, _ in convs]
    latest = dict(
        session.execute(
            select(Message.conversation_id, func.max(Message.id))
            .where(Message.conversation_id.in_(conv_ids))
            .group_by(Message.conversation_id)
        ).all()
    )
    counts = dict(
        session.execute(
            select(Message.conversation_id, func.count(Message.id))
            .where(Message.conversation_id.in_(conv_ids))
            .group_by(Message.conversation_id)
        ).all()
    )
    out = []
    for c, ct in convs[:8]:
        out.append(
            {
                "conversation_id": c.id,
                "contact_name": ct.full_name,
                "contact_email": ct.email,
                "ticket_id": c.hubspot_ticket_id,
                "inquiry_subject": c.inquiry_subject,
                "summary": c.summary,
                "message_count": counts.get(c.id, 0),
                "last_activity": c.last_incoming_at or c.last_outgoing_at or c.created_at,
                "link_message_id": latest.get(c.id),
            }
        )
    return {"domain": domain, "total": len(convs), "rows": out}


# 여기 있던 `_translate_inbound_bubbles` 는 지웠습니다. **화면을 여는 길에는 모델이 없습니다.**
#
# 고객 문의를 한국어로 옮기는 일은 접수할 때 한 번 하고 행에 넣어 둡니다
# (`inbound.cache_korean_inquiries`). 그 함수가 유일한 자리입니다. 열 때 하면 그 티켓을
# 처음 여는 사람이 매번 Gemini 를 기다렸다가 화면을 보고, 번역이 한 번 실패하면 그 티켓은
# 영원히 느려집니다 — 답을 쓰려고 여는 창인데 말입니다.
#
# 회신 초안은 여기서 손대지 않습니다. 초안은 원래 한국어로 쓰이고, 보낼 언어로 바꾸는 것은
# 운영자가 검토 화면에서 `번역하기` 를 누를 때뿐입니다. 미리 해 둘 것은 **한국어가 아닌
# 고객 문의** 하나뿐입니다.
#
# 아직 안 채워진 옛 행은 화면이 원문을 그대로 보여 주고(`body_ko || body`), 10분 폴러가
# 조금씩 채웁니다.


# The two status buckets the operator reviews. "발송대기" is everything a human still
# has to act on; "발송완료" is everything finished, with 거절 distinguished in the row.
# Deliberately absent: "approved" (queued, nothing to decide) and "delivery_unknown"
# (resolved on the 운영 로그 복구 tab, which owns that workflow). A row mid-send carries
# a transient "sending:<pid>:<rand>" status and matches neither, so it hides for the
# few seconds it is claimed instead of rendering that token as a pill.
LIST_STATUS_BUCKETS: dict[str, tuple[str, ...]] = {
    "awaiting": ("pending_approval", "drafting", "draft_failed", "send_failed"),
    # "superseded" = a human answered this ticket in HubSpot while the draft waited, so
    # stage_sync closed it. Finished work, not a decision: it belongs here, and keeping
    # it out of 발송 대기 is the point.
    "sent": ("sent", "test_sent", "rejected", "superseded"),
}
# Stage chips, per status bucket ("" = 전체). The two buckets sit at opposite ends of the
# pipeline, so one shared chip row was wrong in both directions: a reply still waiting
# belongs to a ticket nobody has answered (New) or one under negotiation, and nothing
# else — while sending is exactly what moves a ticket PAST New, so 발송 완료 never has a
# New row and does have the downstream stages. Chips are rendered in PIPELINE_STAGES
# order; a stage that is not in the current bucket falls back to 전체 (see below), which
# is what happens when the operator switches buckets with a stage chip active.
LIST_STAGES: dict[str, tuple[str, ...]] = {
    # 발송 대기 is New only. Drafts are generated for New tickets and nothing else
    # (InboundAgent.handle returns "skipped_not_new" for any other stage), so a
    # Negotiating chip here could only ever return an empty table.
    "awaiting": ("new",),
    "sent": (
        "meeting_link_sent", "negotiation", "reminder_sent",
        "won", "closed_lost", "no_response", "closed",
    ),
}
LIST_SORTS = ("oldest", "newest")


def _messages_list_context(
    status: str = "awaiting",
    stage: str = "",
    sort: str = "oldest",
) -> dict:
    """The approval queue: outgoing drafts and finished replies, never inbound rows.

    Every parameter is validated against a fixed set before it reaches SQL or the
    polling URL in the template — the same allow-list discipline as
    ``VALID_PIPELINE_STAGES``. Unvalidated values would be interpolated into the
    template's ``hx-get`` attribute, where an ``&`` survives escaping and appends a
    parameter to the 15-second poll.
    """
    from .customer_ops import PIPELINE_STAGES, VALID_PIPELINE_STAGES

    status = status if status in LIST_STATUS_BUCKETS else "awaiting"
    # Validated against the CHOSEN bucket's stages, so this also drops a stage the
    # operator carried over from the other bucket instead of returning an empty list.
    stage = stage if stage in LIST_STAGES[status] else ""
    sort = sort if sort in LIST_SORTS else "oldest"

    # Conversation is already joined, so stage / inquiry_subject / created_at /
    # last_incoming_at are select-list additions. Only Contact is new, and
    # Conversation.contact_id is NOT NULL so an inner join loses nothing.
    q = (
        select(
            Message,
            Conversation.stage,
            Conversation.inquiry_subject,
            Conversation.inquiry_category,
            Conversation.created_at,
            Conversation.last_incoming_at,
            Contact.email,
        )
        .join(Conversation, Message.conversation_id == Conversation.id)
        .join(Contact, Conversation.contact_id == Contact.id)
        .where(Message.direction == "outgoing")
        # Auto-ack (접수확인) replies are sent automatically and shown inside the
        # thread — keep them out of the approval queue list so it isn't noisy.
        .where((Message.prompt_variant.is_(None)) | (Message.prompt_variant != "auto_ack"))
        .where(Message.status.in_(LIST_STATUS_BUCKETS[status]))
    )
    if stage:
        q = q.where(Conversation.stage == stage)
    elif status == "awaiting":
        # 발송 대기 is New, always. Drafts are only ever generated for New tickets, so a
        # waiting draft on any later stage means the ticket moved on — somebody answered
        # it in HubSpot while ours sat here. Showing it asks the operator to send a reply
        # the customer already has. LIST_STAGES said "New only"; it only ever constrained
        # the chip, never the query, so the rows leaked in anyway.
        q = q.where(Conversation.stage.in_(LIST_STAGES["awaiting"]))
    # Sort by the column the 접수 시간 cell actually shows, not by our draft's
    # created_at — otherwise "오래된 순" produces a visibly unsorted date column.
    order_column = (
        Conversation.last_incoming_at if stage == "negotiation" else Conversation.created_at
    )
    q = q.order_by(order_column.asc() if sort == "oldest" else order_column.desc()).limit(100)

    with SessionLocal() as session:
        rows = session.execute(q).all()
        messages = [
            {
                "id": msg.id,
                "status": msg.status,
                "stage": conv_stage if conv_stage in VALID_PIPELINE_STAGES else "new",
                # The customer's own subject, shown exactly as HubSpot holds it. The
                # fallback is our REPLY subject (drafting/draft_failed rows can predate
                # the ticket subject), and that one is built as "RE: <original>" — this
                # column is 문의 제목, so the prefix we added comes back off. A "RE:"
                # the CUSTOMER wrote is part of their subject and stays.
                "subject": inquiry_subject or strip_reply_prefixes(msg.subject) or "(제목 없음)",
                # 채널 자리에 있던 값입니다. 전부 "email" 이라 아무 줄도 구분하지 못했고,
                # 그 폭이 정작 궁금한 것 — 이게 무슨 문의인가 — 을 가리고 있었습니다.
                "category": inquiry_category,
                "email": email or "-",
                # New chip → when the ticket arrived; Negotiating → when they last
                # wrote back. Both already on the row, no extra query.
                "received_at": (
                    last_incoming_at if stage == "negotiation" and last_incoming_at else conv_created
                ),
                # Priority is measured from the customer's last message, not from our
                # draft: it answers "how long have they been waiting?".
                "waiting_since": last_incoming_at or conv_created,
            }
            for (
                msg, conv_stage, inquiry_subject, inquiry_category,
                conv_created, last_incoming_at, email,
            ) in rows
        ]
    return {
        "messages": messages,
        "filter_status": status,
        "filter_stage": stage,
        "filter_sort": sort,
        # Built here, not in the template: the labels come from PIPELINE_STAGES, which is
        # also what fixes their order and keeps a renamed stage from needing two edits.
        #
        # Empty when the bucket holds a single stage. 발송 대기 is New-only since the
        # Negotiating chip was dropped, so its row had become 전체 and New — two chips
        # selecting the same rows, and a filter that cannot filter is worse than none.
        "stage_chips": (
            [("", "전체")]
            + [(key, label) for key, label, _ in PIPELINE_STAGES if key in LIST_STAGES[status]]
            if len(LIST_STAGES[status]) > 1
            else []
        ),
        # Same label map as the board and the dashboard — the column must read
        # "New"/"Negotiating", not the raw stage key.
        "stage_labels": {key: label for key, label, _ in PIPELINE_STAGES},
        # 유형 이름과 "세일즈 리드인가" 는 서버가 정합니다 — 분류기가 내는 키와 화면에
        # 보이는 이름이 두 곳에 있으면 새 유형이 빈칸으로 나타납니다.
        "category_labels": CATEGORY_LABELS,
        "unqualified": sorted(UNQUALIFIED),
        "now": list_now().replace(tzinfo=None),
    }


@router.post("/messages/{message_id}/translate")
async def message_translate(
    message_id: int,
    body: str = Form(""),
    subject: str = Form(""),
    signature_key: str = Form(""),
):
    """Translate the Korean draft into the inquiry's language for the operator.

    The reply workflow: the operator reviews/edits a KOREAN draft, then presses
    "번역하기". This translates the (possibly edited) body into the thread's target
    language, washes it, and persists it so it goes out as-is. The subject already
    carries "RE: <original>" in the right language, so it is left untouched.

    Returns JSON {subject, body, language, translated} which the page swaps into
    the editable fields.
    """
    cur_body = body.strip()
    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        if not msg:
            return JSONResponse({"error": "메시지를 찾을 수 없습니다"}, status_code=404)
        if msg.status != "pending_approval":
            return JSONResponse({"error": f"번역 불가 (현재 상태: {msg.status})"}, status_code=400)
        conv_id = msg.conversation_id
        target = (msg.target_language or "").lower()
        cur_body = cur_body or msg.body or ""
        cur_subject = subject.strip() or (msg.subject or "")

        # 서명 선택만 저장합니다. 본문에서 서명을 떼어내던 코드가 여기 있었는데, 이제
        # 모델이 본문에 서명을 쓰지 않으므로 뗄 것이 없습니다(0061). 서명은 발송할 때
        # 본문 아래로 붙습니다.
        msg.signature_key = _clean_signature_key(signature_key)

        # Decide from the BODY's actual language, not the (possibly stale) msg.language
        # flag — so re-editing the draft back to Korean and pressing 번역하기 again
        # actually re-translates. A cheap script check, no LLM: a Korean body and a
        # non-Korean target means there is something to translate.
        needs_tx = bool(target) and target != "ko" and is_mostly_korean(cur_body)
        if not needs_tx:
            washed = text_wash(cur_body)
            msg.body = washed
            if cur_subject:
                msg.subject = cur_subject
            # Keep metadata honest: a non-Korean body for a non-Korean target is
            # already in the send language. Stamping the target onto a KOREAN body is
            # what made a Korean draft claim to be English.
            if target and target != "ko" and not is_mostly_korean(washed):
                msg.language = target
            session.commit()
            return JSONResponse(
                {
                    "body": washed,
                    "subject": cur_subject,
                    "language": msg.language,
                    "translated": False,
                }
            )

        translated = await asyncio.to_thread(translate_to, cur_body, target)
        final_body = text_wash(translated) if translated else text_wash(cur_body)
        msg.body = final_body
        if cur_subject:
            msg.subject = cur_subject
        if translated:
            msg.language = target
        session.commit()

    if translated:
        add_progress(conv_id, "translate", f"회신 초안을 '{target}' 언어로 번역함.")
    return JSONResponse(
        {
            "body": final_body,
            "subject": cur_subject,
            "language": target if translated else "ko",
            "translated": bool(translated),
        }
    )


@router.post("/messages/{message_id}/send")
async def message_send(
    request: Request,
    message_id: int,
    body: str = Form(""),
    subject: str = Form(""),
    signature_key: str = Form(""),
):
    """Approve (and optionally edit) a message, then send it immediately.

    Human approval IS the decision to send, so we dispatch inline here rather than
    leaving the message in 'approved' for the background send worker — a paused or
    absent worker must never strand an already-approved reply.
    """
    if len(body.encode("utf-8")) > _MAX_EDIT_BODY_BYTES:
        return HTMLResponse("<div class='text-red-600 text-sm'>본문이 너무 깁니다.</div>", status_code=400)
    clean_subject = subject.strip()
    clean_body = body.strip()
    if not clean_subject or not clean_body:
        return HTMLResponse(
            "<div class='text-red-600 text-sm'>제목과 본문을 모두 입력해야 발송할 수 있습니다.</div>",
            status_code=400,
        )
    if len(clean_subject) > _MAX_EDIT_SUBJECT_LEN:
        return HTMLResponse("<div class='text-red-600 text-sm'>제목이 너무 깁니다.</div>", status_code=400)

    try:
        approve(
            message_id,
            approver=actor_name(request, fallback="web_ui"),
            edited_body=clean_body,
            edited_subject=clean_subject,
            signature_key=_clean_signature_key(signature_key),
        )
    except ApprovalError as exc:
        return HTMLResponse(
            f'<div class="text-red-600 text-sm">{esc(str(exc))}</div>', status_code=400
        )

    # When the background send worker is running, let IT claim & send this approved
    # row — sending inline here too would let both paths dispatch the same email
    # (the worker claims status='approved'). When the worker is OFF we send inline
    # below so a paused/absent worker never strands an already-approved reply.
    if settings.SEND_WORKER_ENABLED:
        return HTMLResponse(
            '<div class="text-green-600 text-sm font-medium">승인 완료 — 백그라운드 발송 대기 중</div>'
        )

    from ...agents.send_worker import send_approved_now

    if not await send_approved_now(message_id):
        return HTMLResponse(
            '<div class="text-red-600 text-sm font-medium">승인됐지만 발송에 실패했습니다 — 잠시 후 다시 시도해 주세요</div>',
            status_code=500,
        )

    return HTMLResponse('<div class="text-green-600 text-sm font-medium">승인 및 발송 완료</div>')


@router.post("/messages/preview")
async def message_preview(body: str = Form(""), signature_key: str = Form("")):
    """Render a draft body as the HTML email it will become — live approval preview.

    Stateless: takes the (possibly edited) textarea content + the chosen signature
    and returns the same styled HTML the send path attaches, so the approver sees
    the real look.
    """
    from ...integrations.email_html import branded_signature_html, to_html_email

    key = _clean_signature_key(signature_key)
    return HTMLResponse(to_html_email(body, signature_html=branded_signature_html(key)))


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
async def message_edit(
    message_id: int,
    body: str = Form(""),
    subject: str = Form(""),
    signature_key: str = Form(""),
):
    """Save edits to a pending message without sending (body, subject, signature)."""
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
        msg.signature_key = _clean_signature_key(signature_key)
        session.commit()
    return HTMLResponse('<div class="text-blue-600 text-sm font-medium">저장 완료</div>')


_MAX_ROLE_DESC_LEN = 4000


@router.post("/contacts/{contact_id}/edit")
async def contact_edit(contact_id: int, company: str = Form(""), role_description: str = Form("")):
    """Save operator edits to a contact's company + "what they do" note.

    Works even for gmail/unverified senders — the operator fills these in over the
    course of a conversation, and they persist to the DB.
    """
    if len(role_description) > _MAX_ROLE_DESC_LEN:
        return HTMLResponse(
            '<div class="text-red-600 text-sm">설명이 너무 깁니다 (4000자 초과)</div>',
            status_code=413,
        )
    with SessionLocal() as session:
        c = session.get(Contact, contact_id)
        if not c:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">연락처를 찾을 수 없습니다</div>',
                status_code=404,
            )
        c.company = company.strip() or None
        c.role_description = role_description.strip() or None
        session.commit()
    return HTMLResponse(
        '<div class="text-green-600 text-sm font-medium">연락처 정보 저장 완료</div>'
    )
