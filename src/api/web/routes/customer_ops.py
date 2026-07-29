"""Customer history, pipeline, manual touchpoints, contracts, and sales insights."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from ....agents.stage_sync import customer_state_for
from ....common.config import settings
from ....db.models import (
    Contact,
    ContractRecord,
    Conversation,
    CustomerInteraction,
    CustomerProfile,
    Message,
)
from ....db.session import SessionLocal
from ._shared import templates

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

# Days of customer silence (measured from our last outgoing mail) at which each rung
# of the B2B follow-up ladder becomes due: reply -> +3d 1st reminder -> +7d 2nd reminder
# -> +3d Unqualified. The reminder MAIL itself is sent by the HubSpot workflow, not by
# this app; these thresholds only drive the read-only /operations board so an operator
# can see which threads HubSpot is about to act on (and catch ones it missed, e.g. a
# deal that moved to another channel and was never pulled into Negotiating).
FOLLOW_UP_REMINDER_1_DAYS = 3
FOLLOW_UP_REMINDER_2_DAYS = FOLLOW_UP_REMINDER_1_DAYS + 7   # 10
FOLLOW_UP_UNQUALIFIED_DAYS = FOLLOW_UP_REMINDER_2_DAYS + 3  # 13


def _stage_id(stage: str) -> str:
    """Local stage key -> HubSpot stage id. Inverse of stage_sync.local_stage_for()."""
    from ....agents.stage_sync import LOCAL_STAGE_TO_SETTING

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
    from ....integrations.google_sheets import is_configured, update_inbound_stage

    with SessionLocal() as session:
        profile = session.get(CustomerProfile, contact_id)
        qualification = profile.qualification if profile else None
    sheet_result: bool | None = None
    if sheet_client_id and is_configured():
        sheet_result = await asyncio.to_thread(
            update_inbound_stage, sheet_client_id, stage, qualification
        )

    stage_id = _stage_id(stage)
    if not ticket_id or not stage_id:
        return {"sheets": sheet_result, "hubspot": None}
    from ....integrations.hubspot import HubSpotClient, HubSpotNotConfigured

    try:
        client = HubSpotClient()
        await asyncio.to_thread(client.update_ticket_stage_sync, ticket_id, stage_id)
        hubspot_result: bool | None = True
    except HubSpotNotConfigured:
        hubspot_result = False
    except Exception:
        hubspot_result = False
        logger.warning("HubSpot pipeline sync failed for contact %d", contact_id, exc_info=True)
    return {"sheets": sheet_result, "hubspot": hubspot_result}


def _parse_dt(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"날짜 형식이 올바르지 않습니다: {value}") from exc


def _customer_rows() -> list[dict]:
    with SessionLocal() as session:
        contacts = session.execute(select(Contact).order_by(Contact.updated_at.desc())).scalars().all()
        profiles = {p.contact_id: p for p in session.execute(select(CustomerProfile)).scalars()}
        conversations = session.execute(select(Conversation)).scalars().all()
        contracts = session.execute(select(ContractRecord)).scalars().all()

    convs_by_contact: dict[int, list[Conversation]] = defaultdict(list)
    for conv in conversations:
        convs_by_contact[conv.contact_id].append(conv)
    contracts_by_contact: dict[int, list[ContractRecord]] = defaultdict(list)
    for contract in contracts:
        contracts_by_contact[contract.contact_id].append(contract)

    rows: list[dict] = []
    for contact in contacts:
        profile = profiles.get(contact.id)
        convs = convs_by_contact.get(contact.id, [])
        last_activity = max(
            (c.last_incoming_at or c.last_outgoing_at or c.created_at for c in convs),
            default=contact.updated_at,
        )
        active_contract = next(
            (c for c in contracts_by_contact.get(contact.id, []) if c.status == "active"), None
        )
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
                "conversation_count": len(convs),
                "active_contract": active_contract,
            }
        )
    rows.sort(key=lambda row: row["last_activity"] or datetime.min, reverse=True)
    return rows


def _pipeline_rows() -> list[dict]:
    """One board card per inquiry, while contact history stays consolidated."""
    with SessionLocal() as session:
        conversations = session.execute(
            select(Conversation).order_by(Conversation.created_at.desc())
        ).scalars().all()
        contact_ids = {conversation.contact_id for conversation in conversations}
        contacts = {
            contact.id: contact
            for contact in session.execute(select(Contact).where(Contact.id.in_(contact_ids))).scalars()
        } if contact_ids else {}
        profiles = {
            profile.contact_id: profile
            for profile in session.execute(
                select(CustomerProfile).where(CustomerProfile.contact_id.in_(contact_ids))
            ).scalars()
        } if contact_ids else {}

    rows: list[dict] = []
    for conversation in conversations:
        contact = contacts.get(conversation.contact_id)
        if not contact:
            continue
        profile = profiles.get(contact.id)
        # Conversation.stage is the pipeline source of truth; profile is only a
        # customer-summary projection and must not relocate another inquiry.
        stage = conversation.stage if conversation.stage in VALID_PIPELINE_STAGES else "new"
        rows.append(
            {
                "conversation": conversation,
                "contact": contact,
                "profile": profile,
                "stage": stage,
                "last_activity": conversation.last_incoming_at
                or conversation.last_outgoing_at
                or conversation.created_at,
            }
        )
    return rows


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
        session.commit()
        return (
            conversation.hubspot_ticket_id,
            contact.id,
            conversation.sheet_client_id,
        )


@router.get("/customers")
async def customers(request: Request):
    state = request.query_params.get("state", "")
    stage = request.query_params.get("stage", "")
    query = request.query_params.get("q", "").strip().lower()
    rows = _customer_rows()
    if state:
        rows = [row for row in rows if row["state"] == state]
    if stage:
        rows = [row for row in rows if row["stage"] == stage]
    if query:
        rows = [
            row
            for row in rows
            if query
            in " ".join(
                filter(
                    None,
                    [
                        row["contact"].full_name,
                        row["contact"].email,
                        row["contact"].company,
                        row["contact"].domain,
                    ],
                )
            ).lower()
        ]
    return templates.TemplateResponse(
        request,
        "customers.html",
        {"rows": rows, "filter_state": state, "filter_stage": stage, "query": query},
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


@router.get("/customers/{contact_id}")
async def customer_detail(request: Request, contact_id: int):
    ctx = _customer_context(contact_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
    return templates.TemplateResponse(request, "customer_detail.html", ctx)


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


@router.post("/customers/{contact_id}/interactions")
async def interaction_add(
    contact_id: int,
    channel: str = Form("manual"),
    direction: str = Form("note"),
    subject: str = Form(""),
    summary: str = Form(""),
    context: str = Form(""),
    artifact_url: str = Form(""),
    happened_at: str = Form(""),
):
    if not summary.strip():
        return HTMLResponse("내용을 입력해 주세요.", status_code=400)
    with SessionLocal() as session:
        if not session.get(Contact, contact_id):
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        session.add(
            CustomerInteraction(
                contact_id=contact_id,
                channel=channel[:32],
                direction=direction[:16],
                subject=subject.strip()[:300] or None,
                summary=summary.strip(),
                context=context.strip() or None,
                artifact_url=artifact_url.strip() or None,
                happened_at=_parse_dt(happened_at) or datetime.now(timezone.utc),
            )
        )
        if channel == "meeting":
            profile = session.get(CustomerProfile, contact_id) or CustomerProfile(
                contact_id=contact_id
            )
            profile.customer_state = "negotiation"
            profile.pipeline_stage = "negotiation"
            session.add(profile)
        session.commit()
    if channel == "meeting":
        ticket_id, sheet_client_id = _set_local_stage(contact_id, "negotiation")
        await _sync_stage(ticket_id, "negotiation", contact_id, sheet_client_id)
    return RedirectResponse(f"/customers/{contact_id}#history", status_code=303)


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
        from ....agents.sheet_sync import sync_contract_order

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
        from ....agents.sheet_sync import sync_contract_order

        await asyncio.to_thread(sync_contract_order, contract_id)
    return RedirectResponse(f"/customers/{contact_id}#contracts", status_code=303)


def _sync_hubspot(contact_id: int) -> int:
    from ....integrations.hubspot import HubSpotClient

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
async def pipeline_board(request: Request):
    # The Sheets panel is gone from this page, so its context is gone too — building
    # it cost a grant decrypt plus two pending-row counts on every board load.
    from ....agents.hubspot_backfill import hubspot_backfill_status

    rows = _pipeline_rows()
    by_stage = {stage: [] for stage, _, _ in PIPELINE_STAGES}
    for row in rows:
        by_stage.setdefault(row["stage"], []).append(row)
    stage_config = [
        {
            "key": stage,
            "label": label,
            "description": description,
            "hubspot_id": _stage_id(stage),
            "rows": by_stage.get(stage, []),
        }
        for stage, label, description in PIPELINE_STAGES
    ]
    return templates.TemplateResponse(
        request,
        "pipeline.html",
        {
            "stages": stage_config,
            "stage_options": PIPELINE_STAGES,
            "backfill_request": hubspot_backfill_status(),
        },
    )


@router.post("/pipeline/{contact_id}/stage")
async def pipeline_stage_move(contact_id: int, stage: str = Form(...)):
    ticket_id, sheet_client_id = _set_local_stage(contact_id, stage)
    result = await _sync_stage(ticket_id, stage, contact_id, sheet_client_id)
    state = "partial" if False in result.values() else "ok"
    return RedirectResponse(f"/pipeline?sync={state}#stage-{stage}", status_code=303)


@router.post("/pipeline/conversations/{conversation_id}/stage")
async def pipeline_inquiry_stage_move(conversation_id: int, stage: str = Form(...)):
    ticket_id, contact_id, sheet_client_id = _set_conversation_stage(conversation_id, stage)
    result = await _sync_stage(ticket_id, stage, contact_id, sheet_client_id)
    state = "partial" if False in result.values() else "ok"
    return RedirectResponse(f"/pipeline?sync={state}#stage-{stage}", status_code=303)


@router.post("/pipeline/backfill")
async def pipeline_backfill(request: Request):
    """Queue the one-shot HubSpot -> DB backfill (admin only).

    Records a request and returns immediately; the poller performs the work on its
    next tick. HubSpot is read-only here and no mail can result — see
    src/agents/hubspot_backfill.py.
    """
    from ....agents.hubspot_backfill import request_hubspot_backfill
    from ..auth import actor_name

    _require_integration_admin(request)
    try:
        request_hubspot_backfill(actor_name(request, "local-admin"))
    except Exception as exc:
        logger.warning("HubSpot backfill could not be queued.", exc_info=True)
        return RedirectResponse(
            f"/pipeline?backfill=error&detail={quote(str(exc)[:180])}#integrations",
            status_code=303,
        )
    return RedirectResponse("/pipeline?backfill=queued#integrations", status_code=303)


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
    from ....integrations.google_oauth import authorization_url, make_state

    _require_integration_admin(request)
    try:
        state = make_state()
        url = authorization_url(_google_callback_url(request), state)
    except Exception as exc:
        logger.warning("Google Sheets OAuth start failed.", exc_info=True)
        return RedirectResponse(
            f"/pipeline?google=setup_required&detail={quote(str(exc)[:180])}#integrations",
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
    from ....integrations.google_oauth import exchange_code, validate_state

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
            f"/pipeline?google=error&detail={quote(str(exc)[:180])}#integrations",
            status_code=303,
        )
        response.delete_cookie(
            GOOGLE_SHEETS_STATE_COOKIE, path="/integrations/google-sheets/callback"
        )
        return response
    logger.info("Google Sheets user OAuth connected for %s.", account_email or "unknown account")
    response = RedirectResponse("/pipeline?google=connected#integrations", status_code=303)
    response.delete_cookie(
        GOOGLE_SHEETS_STATE_COOKIE, path="/integrations/google-sheets/callback"
    )
    return response


@router.post("/integrations/google-sheets/disconnect")
async def google_sheets_disconnect(request: Request):
    from ....integrations.google_oauth import delete_grant, env_grant

    _require_integration_admin(request)
    if env_grant() is not None:
        # Deleting the row would not disconnect anything — env wins in load_grant().
        # Say so rather than reporting a disconnect that did not happen.
        detail = quote("GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN 으로 연결된 계정입니다. 해제하려면 그 환경변수를 비우세요.")
        return RedirectResponse(
            f"/pipeline?google=error&detail={detail}#integrations", status_code=303
        )
    delete_grant()
    return RedirectResponse("/pipeline?google=disconnected#integrations", status_code=303)


@router.post("/integrations/google-sheets/sync")
async def google_sheets_sync(request: Request):
    from ....agents.sheet_sync import request_full_sheet_sync
    from ..auth import actor_name

    _require_integration_admin(request)
    try:
        request_id = request_full_sheet_sync(actor_name(request, "local-admin"))
    except Exception as exc:
        logger.warning("Google Sheets manual synchronization failed.", exc_info=True)
        return RedirectResponse(
            f"/pipeline?google=error&detail={quote(str(exc)[:180])}#integrations",
            status_code=303,
        )
    return RedirectResponse(
        f"/pipeline?google=queued&request_id={request_id}#integrations",
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


@router.get("/operations")
async def operations(request: Request):
    period = request.query_params.get("period", "month")
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
    return templates.TemplateResponse(
        request,
        "operations.html",
        {
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
        },
    )
