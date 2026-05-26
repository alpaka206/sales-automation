"""Background poller that discovers new HubSpot contacts missed by webhooks."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ..common.config import settings
from ..db.models import Event
from ..db.session import SessionLocal
from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured

logger = logging.getLogger(__name__)

POLL_MARKER_KIND = "inbound_poll_marker"
PROCESSED_KIND = "inbound_processed"
TICKET_POLL_MARKER_KIND = "inbound_ticket_poll_marker"
TICKET_PROCESSED_KIND = "inbound_ticket_processed"


def _get_last_poll_at() -> datetime:
    """Return the last poll timestamp from the events table, or 1 hour ago."""
    with SessionLocal() as session:
        row = (
            session.query(Event)
            .filter(Event.kind == POLL_MARKER_KIND)
            .order_by(Event.created_at.desc())
            .first()
        )
    if row and row.payload and "poll_at" in row.payload:
        return datetime.fromisoformat(row.payload["poll_at"])
    return datetime.now(timezone.utc) - timedelta(hours=1)


def _save_poll_marker(poll_at: datetime) -> None:
    """Persist the current poll timestamp."""
    with SessionLocal() as session:
        session.add(Event(kind=POLL_MARKER_KIND, payload={"poll_at": poll_at.isoformat()}))
        session.commit()


def _is_already_processed(contact_id: str) -> bool:
    """Check if this contact was already handled by webhook or prior poll."""
    with SessionLocal() as session:
        rows = session.query(Event).filter(Event.kind == PROCESSED_KIND).all()
        for r in rows:
            if r.payload and r.payload.get("contact_id") == contact_id:
                return True
    return False


def _mark_processed(contact_id: str) -> None:
    """Record that a contact has been processed."""
    with SessionLocal() as session:
        session.add(Event(kind=PROCESSED_KIND, payload={"contact_id": contact_id}))
        session.commit()


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
    with SessionLocal() as session:
        rows = session.query(Event).filter(Event.kind == TICKET_PROCESSED_KIND).all()
        for r in rows:
            if r.payload and r.payload.get("ticket_id") == ticket_id:
                return True
    return False


def _mark_ticket_processed(ticket_id: str) -> None:
    with SessionLocal() as session:
        session.add(Event(kind=TICKET_PROCESSED_KIND, payload={"ticket_id": ticket_id}))
        session.commit()


def poll_tickets_once() -> int:
    """Discover tickets missed by webhooks. Gated by INBOUND_POLL_TICKETS."""
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

    processed = 0
    for ticket in tickets:
        if _is_ticket_already_processed(ticket.id):
            logger.debug("Skipping already-processed ticket %s", ticket.id)
            continue

        try:
            contact_id = hubspot.get_ticket_primary_contact_sync(ticket.id)
        except Exception:
            logger.exception("Ticket %s: contact lookup failed", ticket.id)
            continue

        if not contact_id:
            logger.info("Ticket %s has no associated contact — marking processed and skipping.", ticket.id)
            _mark_ticket_processed(ticket.id)
            continue

        from .inbound import InboundAgent

        agent = InboundAgent()
        event = {
            "event_type": "ticket_created",
            "object_id": contact_id,
            "ticket_id": ticket.id,
            "occurred_at": now.isoformat(),
        }
        try:
            agent.handle(event)
            _mark_ticket_processed(ticket.id)
            processed += 1
            logger.info("Ticket poll: processed ticket %s (contact %s)", ticket.id, contact_id)
        except Exception:
            logger.exception("Ticket poll: failed to process ticket %s", ticket.id)

    _save_ticket_poll_marker(now)
    logger.info("Ticket poller tick complete: %d tickets processed", processed)
    return processed


def poll_once() -> int:
    """Run a single poll iteration. Returns number of contacts processed."""
    try:
        hubspot = HubSpotClient()
    except HubSpotNotConfigured:
        logger.warning("HubSpot not configured, skipping poll.")
        return 0

    last_poll = _get_last_poll_at()
    now = datetime.now(timezone.utc)

    logger.info("Inbound poller tick: checking contacts created after %s", last_poll.isoformat())

    try:
        contacts = hubspot.search_contacts_sync(created_after=last_poll)
    except Exception:
        logger.exception("HubSpot search failed during poll")
        return 0

    processed = 0
    for contact in contacts:
        if _is_already_processed(contact.id):
            logger.debug("Skipping already-processed contact %s", contact.id)
            continue

        from .inbound import InboundAgent

        agent = InboundAgent()
        event = {
            "event_type": "poll",
            "object_id": contact.id,
            "occurred_at": now.isoformat(),
            "email": contact.email,
            "full_name": " ".join(filter(None, [contact.firstname, contact.lastname])) or "Unknown",
            "company": contact.company,
            "country": contact.country,
            "lifecycle_stage": contact.lifecyclestage,
        }

        try:
            agent.handle(event)
            _mark_processed(contact.id)
            processed += 1
            logger.info("Poll: processed contact %s (%s)", contact.id, contact.email)
        except Exception:
            logger.exception("Poll: failed to process contact %s", contact.id)

    _save_poll_marker(now)
    logger.info("Inbound poller tick complete: %d contacts processed", processed)
    return processed


async def run_poller() -> None:
    """Async loop that calls poll_once() at the configured interval.

    When INBOUND_POLL_TICKETS is on, also calls poll_tickets_once() each tick.
    """
    interval = settings.INBOUND_POLL_INTERVAL_SECONDS
    logger.info(
        "Inbound poller started (interval=%ds, tickets=%s)",
        interval, settings.INBOUND_POLL_TICKETS,
    )

    while True:
        try:
            await asyncio.to_thread(poll_once)
            if settings.INBOUND_POLL_TICKETS:
                await asyncio.to_thread(poll_tickets_once)
        except Exception:
            logger.exception("Inbound poller iteration failed")
        await asyncio.sleep(interval)
