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
STAGE_POLL_MARKER_KIND = "hubspot_stage_poll_marker"
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


def _get_last_stage_poll_at() -> datetime:
    with SessionLocal() as session:
        row = (
            session.query(Event)
            .filter(Event.kind == STAGE_POLL_MARKER_KIND)
            .order_by(Event.created_at.desc())
            .first()
        )
    if row and row.payload and "poll_at" in row.payload:
        return datetime.fromisoformat(row.payload["poll_at"])
    return datetime.now(timezone.utc) - timedelta(hours=settings.INBOUND_INITIAL_LOOKBACK_HOURS)


def reconcile_ticket_stages_once() -> int:
    """Catch HubSpot stage moves the webhook never delivered.

    The webhook is best-effort: a missed delivery, a bulk edit, or an import moves a
    ticket with no ``propertyChange`` reaching us. This sweeps every ticket touched
    since the last run — across ALL stages, unlike ``poll_tickets_once`` which
    deliberately searches only the New stage — and realigns our copy.

    Read-only against HubSpot; the only writes are to our own tables. Returns the
    number of conversations whose stage actually moved.
    """
    try:
        hubspot = HubSpotClient()
    except HubSpotNotConfigured:
        return 0

    from .stage_sync import sync_stage_from_hubspot

    last_poll = _get_last_stage_poll_at()
    now = datetime.now(timezone.utc)
    try:
        tickets = hubspot.search_tickets_sync(
            created_after=last_poll - POLL_OVERLAP,
            pipeline_stage=None,  # every stage — that is the whole point
            limit=POLL_BATCH_SIZE,
        )
    except Exception:
        logger.exception("HubSpot stage reconcile search failed")
        return 0

    changed = 0
    for ticket in tickets:
        try:
            if sync_stage_from_hubspot(ticket.id, ticket.pipeline_stage, source="poller"):
                changed += 1
        except Exception:
            # One bad ticket must not abort the sweep or hold back the watermark.
            logger.exception("Stage reconcile failed for ticket %s", ticket.id)

    if len(tickets) >= POLL_BATCH_SIZE:
        # 페이지가 꽉 찼다는 것은 허브스팟에 더 있다는 뜻이고, 정렬이 오름차순이라 **안 읽은
        # 쪽이 더 최신**입니다. 워터마크를 `now` 로 밀면 그 티켓들은 다음 스윕의 창 밖으로
        # 나가 영영 안 돌아옵니다. 읽은 데까지만 옮깁니다 — `poll_tickets_once` 가 같은 이유로
        # 이미 그렇게 합니다.
        read_upto = [stamp for stamp in map(_ticket_changed_at, tickets) if stamp]
        if read_upto:
            now = min(now, max(read_upto))
    with SessionLocal() as session:
        session.add(Event(kind=STAGE_POLL_MARKER_KIND, payload={"poll_at": now.isoformat()}))
        session.commit()
    if changed:
        logger.info("Stage reconcile: %d conversation(s) realigned to HubSpot", changed)
    return changed


async def run_poller() -> None:
    """Run the polling fallback and sheet backfill at the configured interval."""
    interval = settings.INBOUND_POLL_INTERVAL_SECONDS
    logger.info("Inbound ticket poller started (interval=%ds)", interval)
    while True:
        # **단계마다 따로 잡습니다.** 예전에는 try 하나가 일곱 단계를 전부 감쌌고, 그래서
        # 앞 단계 하나가 터지면 뒤 단계는 그 회차를 통째로 굶었습니다 — 단계 동기화는 두
        # 번째라 앞에 둘이나 있습니다. 한 단계가 실패해도 나머지는 돌아야 합니다.
        for name, run in _poller_steps():
            try:
                await asyncio.to_thread(run)
            except Exception:
                logger.exception("Inbound poller step %s failed", name)
        await asyncio.sleep(interval)


def _poller_steps() -> list[tuple[str, object]]:
    """한 회차에 도는 일들. 순서가 뜻을 갖습니다 — 접수가 먼저, 단계 맞추기가 그다음.

    임포트를 함수 안에 두는 것은 예전 그대로입니다(기동 때 무거운 모듈을 안 끌어옵니다).
    """
    from functools import partial

    from .hubspot_backfill import process_requested_hubspot_backfill
    from .inbound import cache_korean_inquiries
    from .sheet_sync import (
        process_requested_sheet_sync,
        sync_pending_inbound_rows,
        sync_pending_order_rows,
    )
    from .worker_heartbeat import record_worker_heartbeat

    return [
        ("heartbeat", partial(record_worker_heartbeat, "poller", min_interval_seconds=0)),
        ("poll_tickets", poll_tickets_once),
        ("reconcile_stages", reconcile_ticket_stages_once),
        # 접수 때 못 채운 문의 번역을 조금씩 메웁니다 — 이 기능이 생기기 전의 옛 행과,
        # 그때 모델이 안 되던 건입니다. 기다리는 사람이 없는 자리라 여기 둡니다.
        ("cache_korean", cache_korean_inquiries),
        ("hubspot_backfill", process_requested_hubspot_backfill),
        ("sheet_inbound", sync_pending_inbound_rows),
        ("sheet_orders", sync_pending_order_rows),
        ("sheet_full_sync", process_requested_sheet_sync),
    ]
