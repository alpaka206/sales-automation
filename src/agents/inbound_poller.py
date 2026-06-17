"""Background poller that discovers new HubSpot tickets missed by webhooks."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from ..common.config import settings
from ..db.models import Event
from ..db.session import SessionLocal
from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured

logger = logging.getLogger(__name__)

# Requester recovery from a ticket body when the ticket has no associated contact.
# HubSpot email-to-ticket usually links the contact, but manually-created /
# unassociated tickets carry the sender only in the body ("From: Name <email>").
_FROM_RE = re.compile(r"From:\s*(?P<name>[^<\n]*?)\s*<(?P<email>[^>@\s]+@[^>\s]+)>", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _extract_sender(ticket) -> tuple[str | None, str | None]:
    """Return (email, name) of the requester parsed from a ticket's body/subject."""
    text = "\n".join(
        p
        for p in (getattr(ticket, "content", None), getattr(ticket, "subject", None))
        if isinstance(p, str) and p
    )
    m = _FROM_RE.search(text or "")
    if m:
        return m.group("email").strip(), (m.group("name").strip() or None)
    m2 = _EMAIL_RE.search(text or "")
    if m2:
        return m2.group(0).strip(), None
    return None, None


def build_ticket_event(hubspot: HubSpotClient, ticket_id: str, occurred_at: str) -> dict | None:
    """Build an InboundAgent event for a ticket, or None if it can't be replied to.

    Prefers the ticket's associated primary contact (gives email + CRM context).
    When the ticket has no contact, recovers the requester email from the ticket
    body so the ticket still produces a reply draft — inbound is ticket-driven.
    """
    contact_id = hubspot.get_ticket_primary_contact_sync(ticket_id)
    if contact_id:
        return {
            "event_type": "ticket_created",
            "object_id": contact_id,
            "ticket_id": ticket_id,
            "occurred_at": occurred_at,
        }

    ticket = hubspot.get_ticket_sync(ticket_id)
    email, name = _extract_sender(ticket)
    if not email:
        return None
    body = "\n\n".join(p for p in (ticket.subject, ticket.content) if p)
    # Drop the "From: …" header line so the draft reads the inquiry, not metadata.
    body = (_FROM_RE.sub("", body, count=1).strip()) or body
    return {
        "event_type": "ticket_created",
        "object_id": ticket_id,  # stable id for dedup; not a real contact id
        "ticket_id": ticket_id,
        "email": email,
        "full_name": name or email,
        "last_message": body,
        "occurred_at": occurred_at,
    }


TICKET_POLL_MARKER_KIND = "inbound_ticket_poll_marker"
TICKET_PROCESSED_KIND = "inbound_ticket_processed"


def _get_last_ticket_poll_at() -> datetime:
    with SessionLocal() as session:
        row = (
            session.query(Event)
            .filter(Event.kind == TICKET_POLL_MARKER_KIND)
            .order_by(Event.created_at.desc())
            .first()
        )
    if row and row.payload and "poll_at" in row.payload:
        return datetime.fromisoformat(row.payload["poll_at"])
    return datetime.now(timezone.utc) - timedelta(hours=1)


def _save_ticket_poll_marker(poll_at: datetime) -> None:
    with SessionLocal() as session:
        session.add(Event(kind=TICKET_POLL_MARKER_KIND, payload={"poll_at": poll_at.isoformat()}))
        session.commit()


def _is_ticket_already_processed(ticket_id: str) -> bool:
    return ticket_id in _processed_ticket_ids()


def _processed_ticket_ids() -> set[str]:
    """All already-processed ticket ids. Loaded once per ticket poll."""
    with SessionLocal() as session:
        rows = session.query(Event).filter(Event.kind == TICKET_PROCESSED_KIND).all()
    return {r.payload["ticket_id"] for r in rows if r.payload and r.payload.get("ticket_id")}


def _mark_ticket_processed(ticket_id: str) -> None:
    with SessionLocal() as session:
        session.add(Event(kind=TICKET_PROCESSED_KIND, payload={"ticket_id": ticket_id}))
        session.commit()


def poll_tickets_once() -> int:
    """Discover tickets missed by webhooks. Returns number of tickets processed."""
    try:
        hubspot = HubSpotClient()
    except HubSpotNotConfigured:
        logger.warning("HubSpot not configured, skipping ticket poll.")
        return 0

    last_poll = _get_last_ticket_poll_at()
    now = datetime.now(timezone.utc)

    logger.info("Ticket poller tick: checking tickets created after %s", last_poll.isoformat())

    try:
        tickets = hubspot.search_tickets_sync(created_after=last_poll)
    except Exception:
        logger.exception("HubSpot ticket search failed during poll")
        return 0

    seen_tickets = _processed_ticket_ids()
    processed = 0
    for ticket in tickets:
        if ticket.id in seen_tickets:
            logger.debug("Skipping already-processed ticket %s", ticket.id)
            continue

        try:
            event = build_ticket_event(hubspot, ticket.id, now.isoformat())
        except Exception:
            logger.exception("Ticket %s: event build failed", ticket.id)
            continue

        if event is None:
            logger.info(
                "Ticket %s has no contact and no email in body — marking processed and skipping.",
                ticket.id,
            )
            _mark_ticket_processed(ticket.id)
            continue

        from .inbound import InboundAgent

        agent = InboundAgent()
        try:
            agent.handle(event)
            _mark_ticket_processed(ticket.id)
            processed += 1
            logger.info(
                "Ticket poll: processed ticket %s (to %s)", ticket.id, event.get("object_id")
            )
        except Exception:
            logger.exception("Ticket poll: failed to process ticket %s", ticket.id)

    _save_ticket_poll_marker(now)
    logger.info("Ticket poller tick complete: %d tickets processed", processed)
    return processed


async def run_poller() -> None:
    """Async loop that calls poll_tickets_once() at the configured interval."""
    interval = settings.INBOUND_POLL_INTERVAL_SECONDS
    logger.info("Inbound ticket poller started (interval=%ds)", interval)

    while True:
        try:
            await asyncio.to_thread(poll_tickets_once)
        except Exception:
            logger.exception("Inbound poller iteration failed")
        await asyncio.sleep(interval)
