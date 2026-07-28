"""One-shot backfill of the [B2B] AI Dubbing ticket pipeline into our own tables.

The inbound pipeline only ever ingests tickets that arrive in the New stage, so a
portal with years of history shows up here as a single card. This walks every
ticket in the pipeline — all stages — and creates the Contact/Conversation rows the
console renders, so the board reflects reality from day one.

Safety, deliberately:

- **It cannot send mail or draft a reply.** It never calls ``InboundAgent.handle``
  and never enqueues an ``InboundJob``; the inbound worker claims work only from
  ``inbound_jobs`` and the send worker only from ``messages`` with
  ``status='approved'``, so rows created here are invisible to both. No ORM event
  or DB trigger exists that could bridge that gap.
- **It writes nothing to HubSpot.** Reads only — unaffected by the pre-launch guard.
- **It leaves ``last_incoming_at`` NULL.** ``sheet_sync.sync_pending_inbound_rows``
  selects exactly ``sheet_inbound_row IS NULL AND last_incoming_at IS NOT NULL``
  and runs every poller tick; setting it would queue all 300+ rows to be appended
  to the shared sales workbook the moment ``LIVE_EXTERNAL_WRITES`` is turned on,
  and would inflate the pipeline page's "미처리" badge even before that.
- **It creates no Message rows**, so it does not suppress the auto-ack on a thread
  that later receives a genuine inquiry (``is_first_inbound`` counts inbound
  messages, not conversations).

Re-running is safe: contacts key on ``normalized_email`` and conversations on
``hubspot_ticket_id``, both uniquely indexed, and both are upserted.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from ..common.config import settings
from ..common.domains import is_personal_domain
from ..db.models import Contact, Conversation, CustomerProfile, Event
from ..db.session import SessionLocal
from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured
from .stage_sync import local_stage_for

logger = logging.getLogger(__name__)

# Event-driven state machine, mirroring sheet_sync. The status shown in the UI is
# the kind with this prefix stripped, so every kind must read "<prefix>_<status>".
BACKFILL_REQUESTED = "hubspot_backfill_requested"
BACKFILL_STARTED = "hubspot_backfill_started"
BACKFILL_COMPLETED = "hubspot_backfill_completed"
BACKFILL_FAILED = "hubspot_backfill_failed"
BACKFILL_TERMINAL_KINDS = (BACKFILL_COMPLETED, BACKFILL_FAILED)

# The [B2B] AI Dubbing ticket pipeline.
B2B_PIPELINE_ID = "798618015"


def request_hubspot_backfill(actor: str) -> str:
    """Durably enqueue the backfill; the poller runs it on its next tick."""
    request_id = uuid4().hex
    with SessionLocal() as session:
        session.add(
            Event(
                kind=BACKFILL_REQUESTED,
                payload={
                    "request_id": request_id,
                    "actor": actor,
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
        session.commit()
    return request_id


def _pending_request() -> dict | None:
    """The oldest request with no terminal event. STARTED is not terminal, so a
    crash mid-run leaves the request pending and the next tick retries it."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(Event)
            .where(Event.kind.in_((BACKFILL_REQUESTED, *BACKFILL_TERMINAL_KINDS)))
            .order_by(Event.id)
        ).all()
    terminal = {
        str(row.payload.get("request_id"))
        for row in rows
        if row.kind in BACKFILL_TERMINAL_KINDS and row.payload
    }
    for row in rows:
        payload = row.payload or {}
        request_id = str(payload.get("request_id") or "")
        if row.kind == BACKFILL_REQUESTED and request_id and request_id not in terminal:
            return {**payload, "event_id": row.id}
    return None


def hubspot_backfill_status() -> dict | None:
    """Latest backfill request and its durable status, for the pipeline page."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(Event)
            .where(
                Event.kind.in_(
                    (BACKFILL_REQUESTED, BACKFILL_STARTED, *BACKFILL_TERMINAL_KINDS)
                )
            )
            .order_by(Event.id.desc())
        ).all()
    if not rows:
        return None
    request = next((row for row in rows if row.kind == BACKFILL_REQUESTED), None)
    if request is None or not request.payload:
        return None
    request_id = str(request.payload.get("request_id") or "")
    latest = next(
        (
            row
            for row in rows
            if row.payload and str(row.payload.get("request_id") or "") == request_id
        ),
        request,
    )
    return {
        **request.payload,
        **(latest.payload or {}),
        "status": latest.kind.removeprefix("hubspot_backfill_"),
        "updated_at": latest.created_at,
    }


def _display_name(dto) -> str:
    """Contact.full_name is NOT NULL, so always produce something."""
    parts = [p for p in (dto.firstname, dto.lastname) if p and p.strip()]
    if parts:
        return " ".join(parts).strip()
    if dto.email:
        return dto.email.split("@", 1)[0]
    return "이름 미확인"


def backfill_b2b_pipeline(pipeline: str = B2B_PIPELINE_ID) -> dict:
    """Pull every ticket of one pipeline into contacts + conversations.

    Returns counts. Raises HubSpotNotConfigured when there is no token.
    """
    client = HubSpotClient()
    pairs = client.list_tickets_with_contacts_sync(pipeline=pipeline)
    contact_ids = [cid for _ticket, ids in pairs for cid in ids]
    contacts_by_id = client.get_contacts_batch_sync(contact_ids)

    tickets = len(pairs)
    created_contacts = created_convs = updated_convs = skipped = 0

    with SessionLocal() as session:
        for ticket, ids in pairs:
            dto = next((contacts_by_id[i] for i in ids if i in contacts_by_id), None)
            if dto is None:
                # No contact on the ticket (or it was deleted in HubSpot). There is
                # nobody to attribute the inquiry to, so skip rather than invent one.
                skipped += 1
                continue

            email = (dto.email or "").strip().lower()
            normalized = email or f"unknown:hs-{dto.id}"
            contact = session.scalar(
                select(Contact).where(Contact.normalized_email == normalized)
            )
            if contact is None:
                domain = email.split("@", 1)[1] if "@" in email else ""
                contact = Contact(
                    normalized_email=normalized,
                    email=email or None,
                    full_name=_display_name(dto),
                    company=dto.company or None,
                    phone=dto.phone or None,
                    country=dto.country or None,
                    # Personal mailboxes must never be grouped as one company.
                    domain=domain if domain and not is_personal_domain(domain) else None,
                    hubspot_contact_id=dto.id,
                )
                session.add(contact)
                session.flush()
                created_contacts += 1
            else:
                contact.email = contact.email or (email or None)
                contact.company = contact.company or dto.company or None
                contact.phone = contact.phone or dto.phone or None
                contact.country = contact.country or dto.country or None
                contact.hubspot_contact_id = contact.hubspot_contact_id or dto.id

            stage = local_stage_for(ticket.pipeline_stage) or "new"
            conv = session.scalar(
                select(Conversation).where(Conversation.hubspot_ticket_id == ticket.id)
            )
            if conv is None:
                conv = Conversation(
                    contact_id=contact.id,
                    hubspot_ticket_id=ticket.id,
                    stage=stage,
                    topic=ticket.subject or None,
                    # last_incoming_at stays NULL on purpose — see module docstring.
                    created_at=ticket.created_at or datetime.now(timezone.utc),
                )
                session.add(conv)
                created_convs += 1
            elif conv.stage != stage:
                conv.stage = stage
                updated_convs += 1

            profile = session.get(CustomerProfile, contact.id)
            if profile is None:
                profile = CustomerProfile(contact_id=contact.id)
                session.add(profile)
            profile.pipeline_stage = stage

        session.commit()

    result = {
        "tickets": tickets,
        "contacts_created": created_contacts,
        "conversations_created": created_convs,
        "conversations_updated": updated_convs,
        "skipped_no_contact": skipped,
    }
    logger.info("HubSpot backfill finished: %s", result)
    return result


def process_requested_hubspot_backfill() -> bool:
    """Run one pending backfill request. Called from the poller tick."""
    request = _pending_request()
    if request is None:
        return False
    request_id = request["request_id"]

    with SessionLocal() as session:
        session.add(
            Event(
                kind=BACKFILL_STARTED,
                payload={
                    "request_id": request_id,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
        session.commit()

    try:
        counts = backfill_b2b_pipeline()
    except HubSpotNotConfigured as exc:
        _fail(request_id, f"HubSpot 토큰이 설정되지 않았습니다: {exc}")
        return False
    except Exception as exc:
        logger.exception("HubSpot backfill failed")
        _fail(request_id, str(exc))
        return False

    with SessionLocal() as session:
        session.add(
            Event(
                kind=BACKFILL_COMPLETED,
                payload={
                    "request_id": request_id,
                    **counts,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
        session.commit()
    return True


def _fail(request_id: str, error: str) -> None:
    with SessionLocal() as session:
        session.add(
            Event(
                kind=BACKFILL_FAILED,
                payload={
                    "request_id": request_id,
                    "error": error[:500],
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
        session.commit()


__all__ = [
    "B2B_PIPELINE_ID",
    "backfill_b2b_pipeline",
    "hubspot_backfill_status",
    "process_requested_hubspot_backfill",
    "request_hubspot_backfill",
    "settings",
]
