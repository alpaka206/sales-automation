"""Polling fallback that durably enqueues HubSpot tickets missed by webhooks."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ..common.config import settings
from ..db.models import Event
from ..db.session import SessionLocal
from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured
from .inbound_worker import enqueue_inbound_ticket

logger = logging.getLogger(__name__)

TICKET_POLL_MARKER_KIND = "inbound_ticket_poll_marker"
TICKET_PROCESSED_KIND = "inbound_ticket_processed"
POLL_OVERLAP = timedelta(minutes=15)
POLL_BATCH_SIZE = 1000


def _ticket_changed_at(ticket: object) -> datetime | None:
    value = getattr(ticket, "updated_at", None)
    if not isinstance(value, datetime):
        value = getattr(ticket, "created_at", None)
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=value.tzinfo or timezone.utc)


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
    return datetime.now(timezone.utc) - timedelta(hours=settings.INBOUND_INITIAL_LOOKBACK_HOURS)


def _save_ticket_poll_marker(poll_at: datetime) -> None:
    with SessionLocal() as session:
        session.add(Event(kind=TICKET_POLL_MARKER_KIND, payload={"poll_at": poll_at.isoformat()}))
        session.commit()


def _is_ticket_already_processed(ticket_id: str) -> bool:
    return ticket_id in _processed_ticket_ids()


def _processed_ticket_ids() -> set[str]:
    """Compatibility log used by InboundAgent after a successful draft."""
    with SessionLocal() as session:
        rows = session.query(Event).filter(Event.kind == TICKET_PROCESSED_KIND).all()
    return {row.payload["ticket_id"] for row in rows if row.payload and row.payload.get("ticket_id")}


def _mark_ticket_processed(ticket_id: str) -> None:
    with SessionLocal() as session:
        session.add(Event(kind=TICKET_PROCESSED_KIND, payload={"ticket_id": ticket_id}))
        session.commit()


def poll_tickets_once() -> int:
    """Discover and enqueue tickets missed by webhooks."""
    try:
        hubspot = HubSpotClient()
    except HubSpotNotConfigured:
        logger.warning("HubSpot not configured, skipping ticket poll")
        return 0

    last_poll = _get_last_ticket_poll_at()
    search_after = last_poll - POLL_OVERLAP
    now = datetime.now(timezone.utc)
    logger.info("Ticket poller checking tickets changed after %s", search_after.isoformat())
    queued = 0
    cursor = search_after
    while True:
        try:
            tickets = hubspot.search_tickets_sync(
                created_after=cursor,
                pipeline_stage=settings.HUBSPOT_TICKET_STAGE_NEW or None,
                limit=POLL_BATCH_SIZE,
            )
        except Exception:
            logger.exception("HubSpot ticket search failed during poll")
            return queued

        last_changed_at: datetime | None = None
        for ticket in tickets:
            try:
                changed_at = _ticket_changed_at(ticket)
                was_queued = enqueue_inbound_ticket(
                    ticket.id,
                    source="poller",
                    occurred_at=(changed_at or now).isoformat(),
                    event_type="ticket_changed" if changed_at else "ticket_created",
                    occurrence_key=changed_at.isoformat() if changed_at else None,
                )
            except Exception:
                # Keep the old watermark so the next poll repeats this durable write.
                logger.exception("Ticket poll failed to enqueue ticket %s", ticket.id)
                return queued
            if was_queued:
                queued += 1
            if changed_at and (last_changed_at is None or changed_at > last_changed_at):
                last_changed_at = changed_at

        if len(tickets) < POLL_BATCH_SIZE:
            break
        if last_changed_at is None or last_changed_at <= cursor:
            # Advancing the watermark here could drop tickets. Keep it unchanged and
            # retry the overlap window after the malformed/non-advancing result clears.
            logger.error("Ticket poll page was full but had no advancing modification time")
            return queued
        cursor = last_changed_at

    _save_ticket_poll_marker(now)
    logger.info("Ticket poller tick complete: %d tickets queued", queued)
    return queued


async def run_poller() -> None:
    """Run the polling fallback and sheet backfill at the configured interval."""
    interval = settings.INBOUND_POLL_INTERVAL_SECONDS
    logger.info("Inbound ticket poller started (interval=%ds)", interval)
    while True:
        try:
            from .worker_heartbeat import record_worker_heartbeat

            await asyncio.to_thread(
                record_worker_heartbeat, "poller", min_interval_seconds=0
            )
            await asyncio.to_thread(poll_tickets_once)
            from .sheet_sync import (
                process_requested_sheet_sync,
                sync_pending_inbound_rows,
                sync_pending_order_rows,
            )

            await asyncio.to_thread(sync_pending_inbound_rows)
            await asyncio.to_thread(sync_pending_order_rows)
            await asyncio.to_thread(process_requested_sheet_sync)
        except Exception:
            logger.exception("Inbound poller iteration failed")
        await asyncio.sleep(interval)
