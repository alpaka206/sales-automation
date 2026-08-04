"""Customer history, pipeline, manual touchpoints, contracts, and sales insights."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from ...agents.stage_sync import customer_state_for
from ...common.config import settings
from ...db.models import (
    Contact,
    ContractRecord,
    Conversation,
    CustomerInteraction,
    CustomerProfile,
    Message,
)
from ...db.session import SessionLocal

logger = logging.getLogger(__name__)
router = APIRouter(tags=["web"])
GOOGLE_SHEETS_STATE_COOKIE = "perso_sheets_oauth_state"

# Mirrors the [B2B] AI Dubbing ticket pipeline in HubSpot, in flow order. Operators
# also move tickets in HubSpot directly, so every HubSpot stage needs a local key here
# or src/agents/stage_sync.py cannot record where a ticket actually is.
# Legacy keys (follow_up_needed, contracted, onboarding, active) were retired in
# migration 0040; nothing may reintroduce them without a HubSpot stage to match.
PIPELINE_STAGES: tuple[tuple[str, str, str], ...] = (
    ("new", "New", "새 문의"),
    ("meeting_link_sent", "Meeting Link Sent", "답변 발송"),
    ("negotiation", "Negotiating", "협의 중"),
    ("reminder_sent", "Reminder Sent", "리마인더 발송"),
    ("won", "Won", "계약 성사"),
    ("closed_lost", "Lost", "실패"),
    ("closed", "Closed", "협상 전 종료"),
)
VALID_PIPELINE_STAGES = {stage for stage, _, _ in PIPELINE_STAGES}
CONTRACT_STATUSES = {"draft", "sent", "contracted", "active", "expired", "cancelled"}

# Stages where the automated part of the thread is over. Up to 답변 발송 the app owns the
# conversation (auto-acknowledgement, then the reviewed AI reply out through HubSpot);
# from that point the customer answers on whatever channel they prefer — email, WhatsApp,
# phone, SMS — and only the operator knows what was said. So the board offers its 기록
# 추가 (+) button on these stages and not on 새 문의, where nothing has been answered yet.
_STAGE_ORDER = [stage for stage, _, _ in PIPELINE_STAGES]
MANUAL_LOG_STAGES: tuple[str, ...] = tuple(_STAGE_ORDER[_STAGE_ORDER.index("meeting_link_sent") :])

# How many cards one board column renders. A column is a fixed-height scroller and the
# busiest stage here holds 157 threads: nobody drags card 150, and loading them cost a
# full read of every conversation, contact and profile on every dashboard request. The
# header keeps showing the REAL total (see _pipeline_rows), and the column says so when
# it is showing fewer than it counts.
BOARD_CARDS_PER_STAGE = 60

# Logging a 미팅 means the deal is live, and it used to move the thread to 협의 중 from
# WHEREVER it was. That was harmless while the only way to log one was the customer page;
# with a + button on every board card past 답변 발송 it would drag a Won card backwards on
# the next call note. A meeting only ever advances a thread that has not started
# negotiating yet.
_MEETING_ADVANCES_FROM = {"new", "meeting_link_sent"}

# Days of customer silence (measured from our last outgoing mail) at which each rung
# of the B2B follow-up ladder becomes due: reply -> +3d 1st reminder -> +7d 2nd reminder
# -> +3d Unqualified. The reminder MAIL itself is sent by the HubSpot workflow, not by
# this app; these thresholds only drive the read-only /operations board so an operator
# can see which threads HubSpot is about to act on (and catch ones it missed, e.g. a
# deal that moved to another channel and was never pulled into Negotiating).
FOLLOW_UP_REMINDER_1_DAYS = 3
FOLLOW_UP_REMINDER_2_DAYS = FOLLOW_UP_REMINDER_1_DAYS + 7   # 10
FOLLOW_UP_UNQUALIFIED_DAYS = FOLLOW_UP_REMINDER_2_DAYS + 3  # 13


def _announce(topic: str) -> None:
    """Tell every open console something changed. Best effort — a write must never fail
    because nobody was listening."""
    try:
        from .ui_api import publish

        publish(topic)
    except Exception:  # pragma: no cover - broadcasting is not the operation
        logger.debug("Live update broadcast failed for %s", topic, exc_info=True)


def _stage_id(stage: str) -> str:
    """Local stage key -> HubSpot stage id. Inverse of stage_sync.local_stage_for()."""
    from ...agents.stage_sync import LOCAL_STAGE_TO_SETTING

    attr = LOCAL_STAGE_TO_SETTING.get(stage)
    return (getattr(settings, attr, "") or "").strip() if attr else ""


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def _contract_amount(value: str) -> Decimal | None:
    raw = value.replace(",", "").strip()
    if not raw:
        return None
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail="계약 금액은 숫자로 입력해 주세요.") from exc
    if not amount.is_finite():
        raise HTTPException(status_code=400, detail="계약 금액은 유한한 숫자여야 합니다.")
    return amount


def _contract_conversation(session, contact_id: int, raw_id: str) -> Conversation | None:
    """Resolve an explicit inquiry, otherwise use this contact's latest inquiry."""
    if raw_id.strip():
        try:
            conversation_id = int(raw_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="올바른 문의를 선택해 주세요.") from exc
        conversation = session.get(Conversation, conversation_id)
        if not conversation or conversation.contact_id != contact_id:
            raise HTTPException(status_code=400, detail="이 고객의 문의만 계약에 연결할 수 있습니다.")
        return conversation
    return session.scalar(
        select(Conversation)
        .where(Conversation.contact_id == contact_id)
        .order_by(Conversation.created_at.desc(), Conversation.id.desc())
        .limit(1)
    )


def _set_local_stage(contact_id: int, stage: str) -> tuple[str | None, int | None]:
    """Persist the operator's stage and return its HubSpot and Sheets references."""
    if stage not in VALID_PIPELINE_STAGES:
        raise HTTPException(status_code=400, detail="지원하지 않는 파이프라인 단계입니다")
    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        profile = session.get(CustomerProfile, contact_id) or CustomerProfile(contact_id=contact_id)
        profile.pipeline_stage = stage
        profile.customer_state = customer_state_for(stage, profile.customer_state)
        session.add(profile)
        latest_conversation = session.execute(
            select(Conversation)
            .where(
                Conversation.contact_id == contact_id,
            )
            .order_by(Conversation.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest_conversation:
            latest_conversation.stage = stage
        # Capture primitives while the session is open — expire_on_commit=True in
        # production detaches these instances after the `with` block, so reading
        # them in the return tuple would raise DetachedInstanceError.
        ticket_id = latest_conversation.hubspot_ticket_id if latest_conversation else None
        sheet_client_id = (
            latest_conversation.sheet_client_id
            if latest_conversation and latest_conversation.sheet_client_id
            else contact.sheet_client_id
        )
        session.commit()
    return ticket_id, sheet_client_id


async def _sync_stage(
    ticket_id: str | None, stage: str, contact_id: int, sheet_client_id: int | None = None
) -> dict[str, bool | None]:
    """Push a stage the operator just moved to HubSpot and the sales workbook.

    Three-valued per channel, and the distinction is the point: True written, False
    ATTEMPTED AND FAILED, None NOT ATTEMPTED. Not attempted covers a thread with no
    ticket id, an inquiry with no row in the workbook, and pre-launch safe mode — none of
    which is a failure the operator should be warned about. Collapsing "blocked" into
    False made every single card move report 동기화 실패 while the 대전제 is engaged.
    """
    from ...common.safe_mode import ExternalWriteBlocked, live_sheets_writes
    from ...integrations.google_sheets import is_configured, update_inbound_stage

    with SessionLocal() as session:
        profile = session.get(CustomerProfile, contact_id)
        qualification = profile.qualification if profile else None
    sheet_result: bool | None = None
    if sheet_client_id and is_configured():
        sheet_result = (
            await asyncio.to_thread(update_inbound_stage, sheet_client_id, stage, qualification)
            if live_sheets_writes()
            else None
        )

    stage_id = _stage_id(stage)
    if not ticket_id or not stage_id:
        return {"sheets": sheet_result, "hubspot": None}
    from ...integrations.hubspot import HubSpotClient, HubSpotNotConfigured

    try:
        client = HubSpotClient()
        await asyncio.to_thread(client.update_ticket_stage_sync, ticket_id, stage_id)
        hubspot_result: bool | None = True
    except ExternalWriteBlocked:
        # The guard doing its job, not a failure. Must precede the generic handler
        # below — ExternalWriteBlocked is a RuntimeError.
        hubspot_result = None
    except HubSpotNotConfigured:
        hubspot_result = False
    except Exception:
        hubspot_result = False
        logger.warning("HubSpot pipeline sync failed for contact %d", contact_id, exc_info=True)
    return {"sheets": sheet_result, "hubspot": hubspot_result}


def _sync_state(result: dict[str, bool | None]) -> str:
    """The banner flag for what actually happened: partial / ok / local.

    ``local`` is the honest answer when nothing was even attempted — the stage moved in
    this database and nowhere else. It used to render as 동기화 완료, which read as a
    promise that HubSpot and the workbook had been updated.
    """
    if False in result.values():
        return "partial"
    if any(value is True for value in result.values()):
        return "ok"
    return "local"


def _parse_dt(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"날짜 형식이 올바르지 않습니다: {value}") from exc


def _customer_rows() -> list[dict]:
    """One row per contact, newest activity first.

    Three grouped/joined reads instead of four table dumps: this used to pull every
    conversation and every contract into Python only to count them and pick one, which
    is what a GROUP BY and a WHERE are for. The aggregates return one row per contact —
    the size of the page itself — rather than one per conversation.
    """
    activity = (
        select(
            Conversation.contact_id,
            func.count().label("conversations"),
            func.max(Conversation.last_incoming_at).label("incoming"),
            func.max(Conversation.last_outgoing_at).label("outgoing"),
            func.max(Conversation.created_at).label("created"),
        )
        .group_by(Conversation.contact_id)
        .subquery()
    )
    with SessionLocal() as session:
        loaded = session.execute(
            select(Contact, CustomerProfile, activity)
            .outerjoin(CustomerProfile, CustomerProfile.contact_id == Contact.id)
            .outerjoin(activity, activity.c.contact_id == Contact.id)
        ).all()
        # Only the one contract the row shows, not every contract ever signed.
        active_contracts = {
            contract.contact_id: contract
            for contract in session.execute(
                select(ContractRecord)
                .where(ContractRecord.status == "active")
                .order_by(ContractRecord.created_at.desc())
            ).scalars()
        }

    rows: list[dict] = []
    for contact, profile, _cid, conversations, incoming, outgoing, created in loaded:
        # The LATEST of everything that happened, not the first non-null. `incoming or
        # outgoing` returned the customer's last message even when our reply came after
        # it, so a thread answered this morning reported the inquiry's date and sorted
        # below threads nobody had touched in days — under a column headed 최근 활동.
        # ponytail: the three MAXes are reduced here because "greatest of N columns" has
        # no portable SQL spelling (SQLite max(), PostgreSQL GREATEST). Push it into SQL
        # with a dialect switch only if this list ever needs SQL-side paging.
        stamps = [when for when in (incoming, outgoing, created) if when is not None]
        last_activity = max(stamps) if stamps else contact.updated_at
        active_contract = active_contracts.get(contact.id)
        rows.append(
            {
                "contact": contact,
                "profile": profile,
                "state": profile.customer_state if profile else "negotiation",
                "stage": profile.pipeline_stage if profile else "new",
                "temperature": profile.lead_temperature if profile else None,
                "next_action": profile.next_action if profile else None,
                "next_action_at": profile.next_action_at if profile else None,
                "last_activity": last_activity,
                "conversation_count": conversations or 0,
                "active_contract": active_contract,
            }
        )
    rows.sort(key=lambda row: row["last_activity"] or datetime.min, reverse=True)
    return rows


_CARD_ORDER = (Conversation.created_at.desc(), Conversation.id.desc())


def _pipeline_rows(
    *,
    stage: str | None = None,
    limit: int = BOARD_CARDS_PER_STAGE,
    offset: int = 0,
) -> tuple[list[dict], dict[str, int]]:
    """Board cards, newest first — one page of them — plus the true size of each column.

    Returns ``(rows, totals)``. ``totals`` is what the column header shows: paging the
    cards must not quietly change the number an operator reads off the board.

    This used to load EVERY conversation, then every contact, then every profile, then a
    grouped scan of the whole messages table — four unbounded reads on every dashboard
    request, to render columns nobody scrolls to the bottom of. Cost is now set by
    ``limit``, not by how much history exists.

    Two shapes, one body. ``stage=None`` is the board's first paint: a window function
    takes the top ``limit`` of EVERY column in one query. ``stage="won"`` is what the
    column asks for when it is scrolled to the bottom, and needs no window at all — a
    filtered LIMIT/OFFSET. Both then join Contact and CustomerProfile in the same trip
    and look up newest-message ids only for the rows that survived.

    The window function needs SQLite >= 3.25 (2018) and any supported PostgreSQL.
    """
    query = (
        select(Conversation, Contact, CustomerProfile)
        .join(Contact, Conversation.contact_id == Contact.id)
        .outerjoin(CustomerProfile, CustomerProfile.contact_id == Contact.id)
        .order_by(*_CARD_ORDER)
    )
    if stage is None:
        numbered = select(
            Conversation.id.label("conversation_id"),
            func.row_number()
            .over(partition_by=Conversation.stage, order_by=_CARD_ORDER)
            .label("rank"),
        ).subquery()
        query = query.join(numbered, numbered.c.conversation_id == Conversation.id).where(
            numbered.c.rank <= limit
        )
    else:
        query = query.where(Conversation.stage == stage).limit(limit).offset(offset)

    with SessionLocal() as session:
        totals: dict[str, int] = {}
        counts = select(Conversation.stage, func.count()).group_by(Conversation.stage)
        if stage is not None:
            counts = counts.where(Conversation.stage == stage)
        for stage_key, count in session.execute(counts).all():
            key = stage_key if stage_key in VALID_PIPELINE_STAGES else "new"
            totals[key] = totals.get(key, 0) + count

        loaded = session.execute(query).all()

        # Only for the cards actually rendered. The old grouped scan read every row in
        # the messages table to answer a question about at most a few hundred threads.
        conversation_ids = [conversation.id for conversation, _c, _p in loaded]
        latest_message = (
            dict(
                session.execute(
                    select(Message.conversation_id, func.max(Message.id))
                    .where(Message.conversation_id.in_(conversation_ids))
                    .group_by(Message.conversation_id)
                ).all()
            )
            if conversation_ids
            else {}
        )

    rows: list[dict] = []
    for conversation, contact, profile in loaded:
        # Conversation.stage is the pipeline source of truth; profile is only a
        # customer-summary projection and must not relocate another inquiry.
        stage = conversation.stage if conversation.stage in VALID_PIPELINE_STAGES else "new"
        rows.append(
            {
                "conversation": conversation,
                "contact": contact,
                "profile": profile,
                "stage": stage,
                # The workbook's stable key for this inquiry. Threads imported from the
                # sheet carry it on the contact, ones this app appended on the
                # conversation — same fallback order as every stage-sync path.
                "client_id": conversation.sheet_client_id or contact.sheet_client_id,
                # None only for a thread with no message rows at all (a backfilled
                # ticket whose mail was never ingested); the card falls back to the
                # customer page then.
                "link_message_id": latest_message.get(conversation.id),
                # Latest of the three, for the same reason as _customer_rows: `incoming
                # or outgoing` dated a card by the customer's message even when our reply
                # was newer.
                "last_activity": max(
                    when
                    for when in (
                        conversation.last_incoming_at,
                        conversation.last_outgoing_at,
                        conversation.created_at,
                    )
                    if when is not None
                ),
            }
        )
    return rows, totals


def _set_conversation_stage(conversation_id: int, stage: str) -> tuple[str | None, int, int | None]:
    if stage not in VALID_PIPELINE_STAGES:
        raise HTTPException(status_code=400, detail="지원하지 않는 파이프라인 단계입니다")
    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다")
        contact = session.get(Contact, conversation.contact_id)
        if not contact:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        conversation.stage = stage
        latest_id = session.scalar(
            select(Conversation.id)
            .where(Conversation.contact_id == contact.id)
            .order_by(Conversation.created_at.desc(), Conversation.id.desc())
            .limit(1)
        )
        if latest_id == conversation.id:
            profile = session.get(CustomerProfile, contact.id) or CustomerProfile(
                contact_id=contact.id
            )
            profile.pipeline_stage = stage
            profile.customer_state = customer_state_for(stage, profile.customer_state)
            session.add(profile)
        # Fall back to the CONTACT's sheet id, exactly as _set_local_stage and the send
        # worker do. A conversation gets its own id only when this app appended the row;
        # rows imported from the workbook carry it on the contact, and without this
        # fallback a board drop for one of those silently skipped the Sheet.
        sheet_client_id = conversation.sheet_client_id or contact.sheet_client_id
        session.commit()
        return (
            conversation.hubspot_ticket_id,
            contact.id,
            sheet_client_id,
        )


def _customer_context(contact_id: int) -> dict | None:
    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            return None
        profile = session.get(CustomerProfile, contact_id)
        conversations = (
            session.execute(
                select(Conversation)
                .where(Conversation.contact_id == contact_id)
                .order_by(Conversation.created_at.desc())
            )
            .scalars()
            .all()
        )
        conv_ids = [c.id for c in conversations]
        messages = (
            session.execute(
                select(Message)
                .where(Message.conversation_id.in_(conv_ids))
                .order_by(Message.created_at.desc())
            )
            .scalars()
            .all()
            if conv_ids
            else []
        )
        interactions = (
            session.execute(
                select(CustomerInteraction)
                .where(CustomerInteraction.contact_id == contact_id)
                .order_by(CustomerInteraction.happened_at.desc())
            )
            .scalars()
            .all()
        )
        contracts = (
            session.execute(
                select(ContractRecord)
                .where(ContractRecord.contact_id == contact_id)
                .order_by(ContractRecord.created_at.desc())
            )
            .scalars()
            .all()
        )
        same_company = []
        if contact.domain:
            same_company = (
                session.execute(
                    select(Contact)
                    .where(Contact.domain == contact.domain, Contact.id != contact.id)
                    .order_by(Contact.full_name)
                )
                .scalars()
                .all()
            )

        timeline = [
            {
                "channel": m.channel,
                "direction": m.direction,
                "handler": None,
                "subject": m.subject,
                "summary": m.body,
                "context": None,
                "artifact_url": None,
                "happened_at": m.sent_at or m.created_at,
                "source": "message",
            }
            for m in messages
        ]
        timeline.extend(
            {
                "channel": item.channel,
                "direction": item.direction,
                "handler": item.handler,
                "subject": item.subject,
                "summary": item.summary,
                "context": item.context,
                "artifact_url": item.artifact_url,
                "happened_at": item.happened_at,
                "source": "interaction",
            }
            for item in interactions
        )
        timeline.sort(key=lambda item: item["happened_at"] or datetime.min, reverse=True)

        return {
            "contact": contact,
            "profile": profile,
            "conversations": conversations,
            "contracts": contracts,
            "timeline": timeline[:100],
            "same_company": same_company,
            # Drives the pipeline <select>. Hardcoding it here once let this page keep
            # offering stages the board had already dropped, which POSTs then rejected.
            "stage_options": PIPELINE_STAGES,
        }


@router.post("/customers/{contact_id}/profile")
async def customer_profile_save(
    contact_id: int,
    customer_state: str = Form("negotiation"),
    pipeline_stage: str = Form("new"),
    lead_temperature: str = Form(""),
    next_action: str = Form(""),
    next_action_at: str = Form(""),
    industry: str = Form(""),
    user_seq: str = Form(""),
    current_plan: str = Form(""),
    qualification: str = Form(""),
    lost_reason: str = Form(""),
    source: str = Form(""),
    notes: str = Form(""),
):
    valid_states = {"negotiation", "service", "pool", "lost"}
    if customer_state not in valid_states or pipeline_stage not in VALID_PIPELINE_STAGES:
        raise HTTPException(status_code=400, detail="지원하지 않는 고객 상태 또는 파이프라인 단계입니다")
    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        profile = session.get(CustomerProfile, contact_id) or CustomerProfile(contact_id=contact_id)
        session.add(profile)
        profile.customer_state = customer_state
        profile.pipeline_stage = pipeline_stage
        profile.lead_temperature = lead_temperature.strip() or None
        profile.next_action = next_action.strip() or None
        profile.next_action_at = _parse_dt(next_action_at)
        profile.industry = industry.strip() or None
        profile.user_seq = user_seq.strip() or None
        profile.current_plan = current_plan.strip() or None
        profile.qualification = qualification.strip() or None
        profile.lost_reason = lost_reason.strip() or None
        profile.source = source.strip() or None
        profile.notes = notes.strip() or None
        latest_ticket = (
            session.execute(
                select(Conversation)
                .where(
                    Conversation.contact_id == contact_id,
                    Conversation.hubspot_ticket_id.isnot(None),
                )
                .order_by(Conversation.created_at.desc())
            )
            .scalars()
            .first()
        )
        # Capture primitives before the session closes (expire_on_commit=True in
        # production would detach latest_ticket/contact after the `with` block).
        ticket_id = latest_ticket.hubspot_ticket_id if latest_ticket else None
        sheet_client_id = (
            latest_ticket.sheet_client_id
            if latest_ticket and latest_ticket.sheet_client_id
            else contact.sheet_client_id
        )
        session.commit()

    await _sync_stage(ticket_id, pipeline_stage, contact_id, sheet_client_id)
    return RedirectResponse(f"/customers/{contact_id}", status_code=303)


def _internal_path(target: str, fallback: str) -> str:
    """A same-site absolute path, or the fallback. Never an off-site redirect.

    The return address arrives in a form field, so ``//evil.example`` and
    ``https://evil.example`` — both of which a browser follows off this site — are
    rejected, as are the backslash and CR/LF forms used to smuggle them past a naive
    prefix check.
    """
    value = (target or "").strip()
    ok = (
        value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and "\r" not in value
        and "\n" not in value
    )
    return value if ok else fallback


def _linked_conversation(session, raw: str, contact_id: int) -> Conversation | None:
    """The inquiry a manual record belongs to, or None for a contact-wide note.

    The id comes from a hidden form field, so it is checked against THIS contact —
    otherwise an edited field could file one customer's call notes under another
    customer's ticket.
    """
    value = (raw or "").strip()
    if not value.isdigit():
        return None
    conversation = session.get(Conversation, int(value))
    if conversation is None or conversation.contact_id != contact_id:
        return None
    return conversation


@router.post("/customers/{contact_id}/interactions")
async def interaction_add(
    contact_id: int,
    channel: str = Form("manual"),
    direction: str = Form("note"),
    handler: str = Form(""),
    subject: str = Form(""),
    summary: str = Form(""),
    context: str = Form(""),
    artifact_url: str = Form(""),
    happened_at: str = Form(""),
    conversation_id: str = Form(""),
    redirect_to: str = Form(""),
):
    """Record one manual touchpoint — email, WhatsApp, phone, SMS, meeting.

    This is the only way anything that happened after the first reply reaches this
    system: from 답변 발송 onward the thread leaves HubSpot and only the operator knows
    what was said.

    One record is the whole exchange, summarized once. The form used to ask for a
    ``direction`` and that was the wrong question — it made the operator cut one
    conversation into "who spoke" rows. It asks who handled it instead; ``direction``
    keeps its default here and carries a real value only on HubSpot-synced rows.

    ``conversation_id`` files the record under ONE inquiry (the board's + button and the
    ticket screen send it, so the ticket can show its own log); the contact-level form on
    리드 히스토리 leaves it blank and the record stays customer-wide. ``redirect_to``
    returns the operator to where they were instead of the customer page.
    """
    if not summary.strip():
        return HTMLResponse("내용을 입력해 주세요.", status_code=400)
    back = _internal_path(redirect_to, f"/customers/{contact_id}#history")
    # Which thread (if any) the 미팅 rule below should advance. Decided inside the
    # session, applied after the commit — _set_* open their own sessions.
    advance_conversation_id: int | None = None
    advance_contact = False
    with SessionLocal() as session:
        if not session.get(Contact, contact_id):
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        conversation = _linked_conversation(session, conversation_id, contact_id)
        session.add(
            CustomerInteraction(
                contact_id=contact_id,
                conversation_id=conversation.id if conversation else None,
                channel=channel[:32],
                direction=direction[:16],
                handler=handler.strip()[:120] or None,
                subject=subject.strip()[:300] or None,
                summary=summary.strip(),
                context=context.strip() or None,
                artifact_url=artifact_url.strip() or None,
                happened_at=_parse_dt(happened_at) or datetime.now(timezone.utc),
            )
        )
        if channel == "meeting":
            if conversation is not None:
                # A record filed against one ticket moves THAT ticket, never the
                # contact's newest — the operator clicked a specific card. Unknown
                # stages ("initial", pre-0040 keys) read as 새 문의, exactly as the
                # board renders them.
                current = (
                    conversation.stage
                    if conversation.stage in VALID_PIPELINE_STAGES
                    else "new"
                )
                if current in _MEETING_ADVANCES_FROM:
                    advance_conversation_id = conversation.id
            else:
                profile = session.get(CustomerProfile, contact_id) or CustomerProfile(
                    contact_id=contact_id
                )
                # A profile built a line ago has no stage yet — the column default is
                # applied on INSERT, so read it as the 새 문의 it is about to become.
                if (profile.pipeline_stage or "new") in _MEETING_ADVANCES_FROM:
                    profile.customer_state = "negotiation"
                    profile.pipeline_stage = "negotiation"
                    session.add(profile)
                    advance_contact = True
        session.commit()

    if advance_conversation_id is not None:
        ticket_id, _contact_id, sheet_client_id = _set_conversation_stage(
            advance_conversation_id, "negotiation"
        )
        await _sync_stage(ticket_id, "negotiation", contact_id, sheet_client_id)
    elif advance_contact:
        ticket_id, sheet_client_id = _set_local_stage(contact_id, "negotiation")
        await _sync_stage(ticket_id, "negotiation", contact_id, sheet_client_id)
    _announce("interactions")
    return RedirectResponse(back, status_code=303)


@router.post("/customers/{contact_id}/contracts")
async def contract_add(
    contact_id: int,
    conversation_id: str = Form(""),
    status: str = Form("draft"),
    plan: str = Form(""),
    amount: str = Form(""),
    currency: str = Form("KRW"),
    payment_method: str = Form(""),
    payment_instrument: str = Form(""),
    payment_terms: str = Form("일시불"),
    contract_method: str = Form(""),
    billing_email: str = Form(""),
    contract_months: str = Form(""),
    owner_email: str = Form(""),
    space_seq: str = Form(""),
    plan_start_date: str = Form(""),
    enterprise_name: str = Form(""),
    invitation_limit: str = Form(""),
    queue_limit: str = Form(""),
    concurrent_jobs: str = Form(""),
    space_count: str = Form(""),
    contract_credits: str = Form(""),
    credit_history: str = Form(""),
    payer: str = Form(""),
    plan_notes: str = Form(""),
    contract_date: str = Form(""),
    payment_due_at: str = Form(""),
    paid_at: str = Form(""),
    expires_at: str = Form(""),
    language_pairs: str = Form(""),
    unit_price: str = Form(""),
    quote_url: str = Form(""),
    invoice_url: str = Form(""),
    payment_url: str = Form(""),
    notes: str = Form(""),
):
    parsed_amount = _contract_amount(amount)
    if status not in CONTRACT_STATUSES:
        raise HTTPException(status_code=400, detail="지원하지 않는 계약 상태입니다")
    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        conversation = _contract_conversation(session, contact_id, conversation_id)
        sheet_fields = {
            "payment_instrument": payment_instrument.strip(),
            "payment_terms": payment_terms.strip(),
            "contract_method": contract_method.strip(),
            "billing_email": billing_email.strip(),
            "contract_months": contract_months.strip(),
            "owner_email": owner_email.strip(),
            "space_seq": space_seq.strip(),
            "plan_start_date": plan_start_date.strip(),
            "enterprise_name": enterprise_name.strip(),
            "invitation_limit": invitation_limit.strip(),
            "queue_limit": queue_limit.strip(),
            "concurrent_jobs": concurrent_jobs.strip(),
            "space_count": space_count.strip(),
            "contract_credits": contract_credits.strip(),
            "credit_history": credit_history.strip(),
            "payer": payer.strip(),
            "plan_notes": plan_notes.strip(),
        }
        contract = ContractRecord(
            contact_id=contact_id,
            conversation_id=conversation.id if conversation else None,
            sheet_client_id=conversation.sheet_client_id if conversation else None,
            status=status[:32],
            plan=plan.strip() or None,
            amount=parsed_amount,
            currency=currency.strip().upper()[:8] or "KRW",
            payment_method=payment_method.strip() or None,
            contract_date=_parse_dt(contract_date),
            payment_due_at=_parse_dt(payment_due_at),
            paid_at=_parse_dt(paid_at),
            expires_at=_parse_dt(expires_at),
            language_pairs=[v.strip() for v in language_pairs.split(",") if v.strip()] or None,
            unit_price=unit_price.strip() or None,
            quote_url=quote_url.strip() or None,
            invoice_url=invoice_url.strip() or None,
            payment_url=payment_url.strip() or None,
            notes=notes.strip() or None,
            sheet_fields={key: value for key, value in sheet_fields.items() if value},
        )
        session.add(contract)
        # A contract status is NOT a pipeline stage. Until migration 0040 this wrote
        # status straight into pipeline_stage ("contracted"/"active"), which is how
        # those strings became board columns in the first place. The board stage stays
        # the operator's call — saving a contract only settles customer_state.
        if status in {"active", "contracted"}:
            profile = session.get(CustomerProfile, contact_id) or CustomerProfile(
                contact_id=contact_id
            )
            profile.customer_state = "service" if status == "active" else "negotiation"
            profile.current_plan = plan.strip() or profile.current_plan
            session.add(profile)
        session.commit()
        contract_id = contract.id

    if status in {"active", "contracted"}:
        from ...agents.sheet_sync import sync_contract_order

        await asyncio.to_thread(sync_contract_order, contract_id)
    return RedirectResponse(f"/customers/{contact_id}#contracts", status_code=303)


@router.post("/customers/{contact_id}/contracts/{contract_id}")
async def contract_update(
    contact_id: int,
    contract_id: int,
    conversation_id: str = Form(""),
    status: str = Form("draft"),
    plan: str = Form(""),
    amount: str = Form(""),
    currency: str = Form("KRW"),
    payment_method: str = Form(""),
    contract_date: str = Form(""),
    payment_due_at: str = Form(""),
    paid_at: str = Form(""),
    expires_at: str = Form(""),
    language_pairs: str = Form(""),
    unit_price: str = Form(""),
    quote_url: str = Form(""),
    invoice_url: str = Form(""),
    payment_url: str = Form(""),
    notes: str = Form(""),
):
    """Correct operator-owned contract facts without creating a duplicate row."""
    parsed_amount = _contract_amount(amount)
    if status not in CONTRACT_STATUSES:
        raise HTTPException(status_code=400, detail="지원하지 않는 계약 상태입니다")

    with SessionLocal() as session:
        contract = session.get(ContractRecord, contract_id)
        contact = session.get(Contact, contact_id)
        if not contact or not contract or contract.contact_id != contact_id:
            raise HTTPException(status_code=404, detail="계약을 찾을 수 없습니다")
        conversation = _contract_conversation(session, contact_id, conversation_id)
        contract.conversation_id = conversation.id if conversation else None
        contract.sheet_client_id = conversation.sheet_client_id if conversation else None
        contract.status = status
        contract.plan = plan.strip() or None
        contract.amount = parsed_amount
        contract.currency = currency.strip().upper()[:8] or "KRW"
        contract.payment_method = payment_method.strip() or None
        contract.contract_date = _parse_dt(contract_date)
        contract.payment_due_at = _parse_dt(payment_due_at)
        contract.paid_at = _parse_dt(paid_at)
        contract.expires_at = _parse_dt(expires_at)
        contract.language_pairs = [v.strip() for v in language_pairs.split(",") if v.strip()] or None
        contract.unit_price = unit_price.strip() or None
        contract.quote_url = quote_url.strip() or None
        contract.invoice_url = invoice_url.strip() or None
        contract.payment_url = payment_url.strip() or None
        contract.notes = notes.strip() or None
        contract.sheet_synced_at = None
        session.commit()

    # As in contract_add: the contract status no longer drives the board stage.
    if status in {"contracted", "active"}:
        from ...agents.sheet_sync import sync_contract_order

        await asyncio.to_thread(sync_contract_order, contract_id)
    return RedirectResponse(f"/customers/{contact_id}#contracts", status_code=303)


def _sync_hubspot(contact_id: int) -> int:
    from ...integrations.hubspot import HubSpotClient

    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact or not contact.hubspot_contact_id:
            raise ValueError("HubSpot 연락처 ID가 없습니다")
        hubspot_id = contact.hubspot_contact_id

    client = HubSpotClient()
    dto = client.get_contact_sync(hubspot_id)
    emails = client.get_recent_emails_sync(hubspot_id, limit=20)
    deals = client.get_associated_deals_sync(hubspot_id)
    note = client.get_latest_note(hubspot_id)
    inserted = 0
    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            return 0
        contact.email = dto.email or contact.email
        contact.full_name = " ".join(filter(None, [dto.firstname, dto.lastname])) or contact.full_name
        contact.company = dto.company or contact.company
        contact.phone = dto.phone or contact.phone
        contact.country = dto.country or contact.country
        contact.lifecycle_stage = dto.lifecyclestage or contact.lifecycle_stage
        profile = session.get(CustomerProfile, contact_id) or CustomerProfile(contact_id=contact_id)
        profile.last_synced_at = datetime.now(timezone.utc)
        session.add(profile)

        for email in emails:
            external_id = f"hubspot:email:{email.id}"
            exists = session.scalar(
                select(CustomerInteraction.id).where(CustomerInteraction.external_id == external_id)
            )
            if not exists:
                session.add(
                    CustomerInteraction(
                        contact_id=contact_id,
                        channel="email",
                        direction="incoming" if "incoming" in email.type else "outgoing",
                        subject=email.subject,
                        summary=email.body or email.subject or "HubSpot 이메일",
                        external_id=external_id,
                        happened_at=email.timestamp or datetime.now(timezone.utc),
                    )
                )
                inserted += 1
        for deal in deals:
            external_id = f"hubspot:deal:{deal.id}"
            exists = session.scalar(
                select(CustomerInteraction.id).where(CustomerInteraction.external_id == external_id)
            )
            if not exists:
                session.add(
                    CustomerInteraction(
                        contact_id=contact_id,
                        channel="hubspot",
                        direction="note",
                        subject=deal.name or "HubSpot Deal",
                        summary=f"단계: {deal.stage or '-'} · 금액: {deal.amount or '-'}",
                        external_id=external_id,
                    )
                )
                inserted += 1
        if note:
            digest = hashlib.sha256(note.encode("utf-8")).hexdigest()[:16]
            external_id = f"hubspot:note:{digest}"
            exists = session.scalar(
                select(CustomerInteraction.id).where(CustomerInteraction.external_id == external_id)
            )
            if not exists:
                session.add(
                    CustomerInteraction(
                        contact_id=contact_id,
                        channel="hubspot",
                        direction="note",
                        subject="HubSpot 메모",
                        summary=note,
                        external_id=external_id,
                    )
                )
                inserted += 1
        session.commit()
    return inserted


@router.post("/customers/{contact_id}/sync")
async def customer_sync(contact_id: int):
    try:
        await asyncio.to_thread(_sync_hubspot, contact_id)
    except Exception as exc:
        logger.warning("Customer HubSpot sync failed for %d", contact_id, exc_info=True)
        raise HTTPException(status_code=502, detail=f"HubSpot 동기화 실패: {exc}") from exc
    return RedirectResponse(f"/customers/{contact_id}", status_code=303)


@router.get("/pipeline")
async def pipeline_board_redirect():
    """The board moved onto the dashboard; keep bookmarks and old links working.

    The POST actions below keep their /pipeline/... paths — security.py:24 and the
    board's own drag-drop fetch are both pinned to that prefix — so only the page
    itself moved. They now redirect to / where the board actually renders.
    """
    return RedirectResponse("/", status_code=308)


@router.post("/pipeline/{contact_id}/stage")
async def pipeline_stage_move(contact_id: int, stage: str = Form(...)):
    ticket_id, sheet_client_id = _set_local_stage(contact_id, stage)
    result = await _sync_stage(ticket_id, stage, contact_id, sheet_client_id)
    _announce("pipeline")
    return RedirectResponse(f"/?sync={_sync_state(result)}#stage-{stage}", status_code=303)


@router.post("/pipeline/conversations/{conversation_id}/stage")
async def pipeline_inquiry_stage_move(conversation_id: int, stage: str = Form(...)):
    """The board's drop target. The local move is committed first and always sticks;
    HubSpot and the workbook follow, and the ?sync flag says which of them actually did."""
    ticket_id, contact_id, sheet_client_id = _set_conversation_stage(conversation_id, stage)
    result = await _sync_stage(ticket_id, stage, contact_id, sheet_client_id)
    _announce("pipeline")
    return RedirectResponse(f"/?sync={_sync_state(result)}#stage-{stage}", status_code=303)


@router.post("/pipeline/backfill")
async def pipeline_backfill(request: Request):
    """Queue the one-shot HubSpot -> DB backfill (admin only).

    Records a request and returns immediately; the poller performs the work on its
    next tick. HubSpot is read-only here and no mail can result — see
    src/agents/hubspot_backfill.py.
    """
    from ...agents.hubspot_backfill import request_hubspot_backfill
    from ..auth import actor_name

    _require_integration_admin(request)
    try:
        request_hubspot_backfill(actor_name(request, "local-admin"))
    except Exception as exc:
        logger.warning("HubSpot backfill could not be queued.", exc_info=True)
        return RedirectResponse(
            f"/?backfill=error&detail={quote(str(exc)[:180])}",
            status_code=303,
        )
    return RedirectResponse("/?backfill=queued", status_code=303)


def _google_callback_url(request: Request) -> str:
    base = settings.PUBLIC_BASE_URL.strip().rstrip("/")
    if not base:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        base = f"{scheme}://{request.url.netloc}"
    return f"{base}/integrations/google-sheets/callback"


def _require_integration_admin(request: Request) -> None:
    if settings.AUTH_MODE != "google_oauth":
        return
    from ..auth import is_admin

    if not is_admin(request):
        raise HTTPException(status_code=403, detail="관리자만 외부 계정을 연결할 수 있습니다")


@router.get("/integrations/google-sheets/connect")
async def google_sheets_connect(request: Request):
    from ...integrations.google_oauth import authorization_url, make_state

    _require_integration_admin(request)
    try:
        state = make_state()
        url = authorization_url(_google_callback_url(request), state)
    except Exception as exc:
        logger.warning("Google Sheets OAuth start failed.", exc_info=True)
        return RedirectResponse(
            f"/?google=setup_required&detail={quote(str(exc)[:180])}",
            status_code=303,
        )
    response = RedirectResponse(url, status_code=302)
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        GOOGLE_SHEETS_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=proto == "https",
        samesite="lax",
        path="/integrations/google-sheets/callback",
    )
    return response


@router.get("/integrations/google-sheets/callback")
async def google_sheets_callback(
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
):
    from ...integrations.google_oauth import exchange_code, validate_state

    _require_integration_admin(request)
    try:
        expected_state = request.cookies.get(GOOGLE_SHEETS_STATE_COOKIE, "")
        if not expected_state or not hmac.compare_digest(expected_state, state):
            raise ValueError("Google 연결 요청 세션이 일치하지 않습니다. 다시 연결해 주세요.")
        validate_state(state)
        if error:
            raise ValueError(f"Google 연결이 취소되었습니다: {error[:120]}")
        if not code:
            raise ValueError("Google authorization code is missing.")
        _payload, account_email = await exchange_code(code, _google_callback_url(request))
    except Exception as exc:
        logger.warning("Google Sheets OAuth callback failed.", exc_info=True)
        response = RedirectResponse(
            f"/?google=error&detail={quote(str(exc)[:180])}",
            status_code=303,
        )
        response.delete_cookie(
            GOOGLE_SHEETS_STATE_COOKIE, path="/integrations/google-sheets/callback"
        )
        return response
    logger.info("Google Sheets user OAuth connected for %s.", account_email or "unknown account")
    response = RedirectResponse("/?google=connected", status_code=303)
    response.delete_cookie(
        GOOGLE_SHEETS_STATE_COOKIE, path="/integrations/google-sheets/callback"
    )
    return response


@router.post("/integrations/google-sheets/disconnect")
async def google_sheets_disconnect(request: Request):
    from ...integrations.google_oauth import delete_grant, env_grant

    _require_integration_admin(request)
    if env_grant() is not None:
        # Deleting the row would not disconnect anything — env wins in load_grant().
        # Say so rather than reporting a disconnect that did not happen.
        detail = quote("GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN 으로 연결된 계정입니다. 해제하려면 그 환경변수를 비우세요.")
        return RedirectResponse(
            f"/?google=error&detail={detail}", status_code=303
        )
    delete_grant()
    return RedirectResponse("/?google=disconnected", status_code=303)


@router.post("/integrations/google-sheets/sync")
async def google_sheets_sync(request: Request):
    from ...agents.sheet_sync import request_full_sheet_sync
    from ..auth import actor_name

    _require_integration_admin(request)
    try:
        request_id = request_full_sheet_sync(actor_name(request, "local-admin"))
    except Exception as exc:
        logger.warning("Google Sheets manual synchronization failed.", exc_info=True)
        return RedirectResponse(
            f"/?google=error&detail={quote(str(exc)[:180])}",
            status_code=303,
        )
    return RedirectResponse(
        f"/?google=queued&request_id={request_id}",
        status_code=303,
    )


def _month_start(value: datetime, months_back: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - months_back
    return datetime(month_index // 12, month_index % 12 + 1, 1)


def _bucket_key(value: datetime, period: str):
    if period == "year":
        return value.year
    if period == "month":
        return value.year, value.month
    return value.date()


def _inbound_analytics(period: str) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if period == "year":
        starts = [datetime(now.year - offset, 1, 1) for offset in range(4, -1, -1)]
        labels = [str(value.year) for value in starts]
    elif period == "month":
        starts = [_month_start(now, offset) for offset in range(11, -1, -1)]
        labels = [value.strftime("%y.%m") for value in starts]
    else:
        period = "day"
        today = datetime(now.year, now.month, now.day)
        starts = [today - timedelta(days=offset) for offset in range(29, -1, -1)]
        labels = [value.strftime("%m/%d") for value in starts]

    start = starts[0]
    keys = [_bucket_key(value, period) for value in starts]
    index = {key: idx for idx, key in enumerate(keys)}
    counts = [0 for _ in starts]
    countries: list[Counter[str]] = [Counter() for _ in starts]
    scores: list[int] = []
    before = 0
    with SessionLocal() as session:
        records = session.execute(
            select(Message.created_at, Message.score_snapshot, Contact.country, Contact.score)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(Contact, Conversation.contact_id == Contact.id)
            .where(Message.direction == "inbound")
        ).all()

    for created_at, score_snapshot, country, contact_score in records:
        created = _naive(created_at)
        if not created or created < start:
            before += 1
            continue
        idx = index.get(_bucket_key(created, period))
        if idx is None or created > now:
            continue
        counts[idx] += 1
        countries[idx][(country or "국가 미확인").strip() or "국가 미확인"] += 1
        score = score_snapshot if score_snapshot is not None else contact_score
        if score is not None:
            scores.append(int(score))

    cumulative: list[int] = []
    running = before
    for count in counts:
        running += count
        cumulative.append(running)

    max_count = max(counts, default=0) or 1
    max_cumulative = max(cumulative, default=0) or 1
    chart = []
    for idx, (label, count, total) in enumerate(zip(labels, counts, cumulative, strict=True)):
        x = 0 if len(starts) == 1 else idx * 1000 / (len(starts) - 1)
        y = 230 - (total / max_cumulative * 205)
        chart.append(
            {
                "label": label,
                "count": count,
                "total": total,
                "bar_height": round(count / max_count * 100, 2),
                "x": round(x, 2),
                "y": round(y, 2),
                "show_label": period != "day" or idx % 5 == 0 or idx == len(starts) - 1,
            }
        )

    country_totals: Counter[str] = Counter()
    for bucket in countries:
        country_totals.update(bucket)
    country_rows = []
    largest_country = max(country_totals.values(), default=0) or 1
    for country, total in country_totals.most_common():
        country_rows.append(
            {
                "country": country,
                "total": total,
                "share": round(total / max(sum(counts), 1) * 100, 1),
                "width": round(total / largest_country * 100, 1),
                "trend": [bucket.get(country, 0) for bucket in countries],
            }
        )

    return {
        "period": period,
        "chart": chart,
        "line_points": " ".join(f'{point["x"]},{point["y"]}' for point in chart),
        "country_rows": country_rows,
        "inbound_in_period": sum(counts),
        "inbound_total": cumulative[-1] if cumulative else 0,
        "average_score": round(sum(scores) / len(scores), 1) if scores else None,
        "qualified_count": sum(score >= 70 for score in scores),
    }


def _operations_context(period: str) -> dict:
    """인사이트. Extracted from the route so the React screen reads the same numbers —
    a second copy of this arithmetic is a second set of answers to 몇 건이냐."""
    if period not in {"day", "month", "year"}:
        period = "month"
    analytics = _inbound_analytics(period)
    rows = _customer_rows()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_before = now - timedelta(days=14)
    reminder_1_before = now - timedelta(days=FOLLOW_UP_REMINDER_1_DAYS)
    reminder_2_before = now - timedelta(days=FOLLOW_UP_REMINDER_2_DAYS)
    unqualified_before = now - timedelta(days=FOLLOW_UP_UNQUALIFIED_DAYS)
    renew_before = now + timedelta(days=60)
    with SessionLocal() as session:
        conversations = session.execute(select(Conversation)).scalars().all()
        renewals = (
            session.execute(
                select(ContractRecord, Contact)
                .join(Contact, ContractRecord.contact_id == Contact.id)
                .where(
                    ContractRecord.status == "active",
                    ContractRecord.expires_at.isnot(None),
                    ContractRecord.expires_at <= renew_before,
                )
                .order_by(ContractRecord.expires_at)
            )
            .all()
        )
    row_by_contact = {row["contact"].id: row for row in rows}
    stale = [
        row
        for row in rows
        if row["state"] == "negotiation"
        and row["last_activity"]
        and row["last_activity"].replace(tzinfo=None) < stale_before
    ]
    missing_reply = []
    due_reminder_1 = []
    due_reminder_2 = []
    due_unqualified = []
    for conv in conversations:
        incoming = _naive(conv.last_incoming_at)
        outgoing = _naive(conv.last_outgoing_at)
        if incoming and (not outgoing or incoming > outgoing):
            row = row_by_contact.get(conv.contact_id)
            if row and row not in missing_reply:
                missing_reply.append(row)
            continue
        # Waiting on the customer: we mailed last and they have not answered since.
        # Buckets are exclusive so the counts add up - each thread shows on the one
        # rung of the ladder it currently sits at.
        if not outgoing or (incoming and incoming >= outgoing):
            continue
        row = row_by_contact.get(conv.contact_id)
        if not row:
            continue
        if outgoing < unqualified_before:
            bucket = due_unqualified
        elif outgoing < reminder_2_before:
            bucket = due_reminder_2
        elif outgoing < reminder_1_before:
            bucket = due_reminder_1
        else:
            continue
        if row not in bucket:
            bucket.append(row)
    lost = [row for row in rows if row["stage"] == "closed_lost" or row["state"] == "lost"]
    upsell = [
        row
        for row in rows
        if row["state"] == "service"
        and (not row["profile"] or (row["profile"].current_plan or "").lower() not in {"business", "enterprise"})
    ]
    return {
            **analytics,
            "stale": stale,
            "missing_reply": missing_reply,
            "due_reminder_1": due_reminder_1,
            "due_reminder_2": due_reminder_2,
            "due_unqualified": due_unqualified,
            "follow_up_days": {
                "reminder_1": FOLLOW_UP_REMINDER_1_DAYS,
                "reminder_2": FOLLOW_UP_REMINDER_2_DAYS,
                "unqualified": FOLLOW_UP_UNQUALIFIED_DAYS,
            },
            "renewals": renewals,
            "lost": lost,
            "upsell": upsell,
    }


