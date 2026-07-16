"""Customer history, pipeline, manual touchpoints, contracts, and sales insights."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

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
    valid_stages = {
        "new",
        "meeting_link_sent",
        "negotiation",
        "contracted",
        "onboarding",
        "active",
        "closed_lost",
    }
    if customer_state not in valid_states or pipeline_stage not in valid_stages:
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
        session.commit()

    stage_id = None
    if pipeline_stage == "new":
        stage_id = settings.HUBSPOT_TICKET_STAGE_NEW
    elif pipeline_stage == "meeting_link_sent":
        stage_id = settings.HUBSPOT_TICKET_STAGE_AFTER_SEND
    if latest_ticket and stage_id:
        from ....integrations.hubspot import HubSpotClient, HubSpotNotConfigured

        try:
            client = HubSpotClient()
            await asyncio.to_thread(
                client.update_ticket_stage_sync, latest_ticket.hubspot_ticket_id, stage_id
            )
        except HubSpotNotConfigured:
            pass
        except Exception:
            logger.warning("HubSpot pipeline sync failed for contact %d", contact_id, exc_info=True)
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
    return RedirectResponse(f"/customers/{contact_id}#history", status_code=303)


@router.post("/customers/{contact_id}/contracts")
async def contract_add(
    contact_id: int,
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
    try:
        parsed_amount = float(amount.replace(",", "")) if amount.strip() else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="계약 금액은 숫자로 입력해 주세요") from exc
    with SessionLocal() as session:
        if not session.get(Contact, contact_id):
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        contract = ContractRecord(
                contact_id=contact_id,
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
            )
        session.add(contract)
        if status == "active":
            profile = session.get(CustomerProfile, contact_id) or CustomerProfile(
                contact_id=contact_id
            )
            profile.customer_state = "service"
            profile.pipeline_stage = "active"
            profile.current_plan = plan.strip() or profile.current_plan
            session.add(profile)
        session.commit()
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
        contact.whatsapp_opt_in = dto.whatsapp_opt_in or contact.whatsapp_opt_in
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


@router.get("/operations")
async def operations(request: Request):
    rows = _customer_rows()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_before = now - timedelta(days=14)
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
    for conv in conversations:
        incoming = conv.last_incoming_at
        outgoing = conv.last_outgoing_at
        if incoming and (not outgoing or incoming > outgoing):
            row = row_by_contact.get(conv.contact_id)
            if row and row not in missing_reply:
                missing_reply.append(row)
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
            "stale": stale,
            "missing_reply": missing_reply,
            "renewals": renewals,
            "lost": lost,
            "upsell": upsell,
        },
    )
