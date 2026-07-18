"""Small database-backed worker for reliable HubSpot inbound processing."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from ..db.models import InboundJob
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
LEASE_SECONDS = 30 * 60
HEARTBEAT_SECONDS = LEASE_SECONDS // 3
IDLE_SECONDS = 2.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def inbound_event_key(ticket_id: str, occurrence_key: str | None = None) -> str:
    """One stable key shared by webhook and polling discovery."""
    ticket_id = str(ticket_id).strip()
    if not ticket_id:
        raise ValueError("ticket_id is required")
    suffix = f"changed:{occurrence_key}" if occurrence_key else "created"
    return f"hubspot:ticket:{ticket_id}:{suffix}"


def enqueue_inbound_ticket(
    ticket_id: str,
    *,
    source: str,
    occurred_at: str | None = None,
    hubspot_event_id: str | None = None,
    event_type: str = "ticket_created",
    occurrence_key: str | None = None,
) -> bool:
    """Persist a ticket for processing. Returns true when newly queued/rearmed."""
    now = _utcnow()
    event_key = inbound_event_key(ticket_id, occurrence_key)
    payload = {
        "ticket_id": str(ticket_id),
        "event_type": event_type,
        "occurred_at": occurred_at,
        "hubspot_event_id": hubspot_event_id,
    }
    with SessionLocal() as session:
        session.add(
            InboundJob(
                event_key=event_key,
                source=source,
                payload=payload,
                status="pending",
                available_at=now,
            )
        )
        try:
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            existing = session.query(InboundJob).filter_by(event_key=event_key).first()
            if existing and existing.status == "dead":
                existing.status = "pending"
                existing.attempts = 0
                existing.available_at = now
                existing.locked_at = None
                existing.locked_by = None
                existing.last_error = None
                existing.updated_at = now
                session.commit()
                return True
            return False


def _claim_next_job() -> tuple[int, dict, int, str] | None:
    now = _utcnow()
    stale_before = now - timedelta(seconds=LEASE_SECONDS)
    ready = or_(
        and_(InboundJob.status == "pending", InboundJob.available_at <= now),
        and_(InboundJob.status == "processing", InboundJob.locked_at <= stale_before),
    )

    with SessionLocal() as session:
        # A worker that died on its final attempt must not leave a permanent lease.
        session.query(InboundJob).filter(
            InboundJob.status == "processing",
            InboundJob.attempts >= MAX_ATTEMPTS,
            InboundJob.locked_at <= stale_before,
        ).update(
            {
                InboundJob.status: "dead",
                InboundJob.locked_at: None,
                InboundJob.locked_by: None,
                InboundJob.updated_at: now,
            },
            synchronize_session=False,
        )
        session.commit()

        candidates = (
            session.query(InboundJob.id)
            .filter(ready, InboundJob.attempts < MAX_ATTEMPTS)
            .order_by(InboundJob.available_at.asc(), InboundJob.id.asc())
            .limit(10)
            .all()
        )
        for (job_id,) in candidates:
            owner = uuid4().hex
            claimed = (
                session.query(InboundJob)
                .filter(InboundJob.id == job_id, ready, InboundJob.attempts < MAX_ATTEMPTS)
                .update(
                    {
                        InboundJob.status: "processing",
                        InboundJob.attempts: InboundJob.attempts + 1,
                        InboundJob.locked_at: now,
                        InboundJob.locked_by: owner,
                        InboundJob.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            if not claimed:
                session.rollback()
                continue
            session.commit()
            job = session.get(InboundJob, job_id)
            if job:
                return job.id, dict(job.payload), job.attempts, owner
    return None


def _renew_job_lease(job_id: int, owner: str) -> bool:
    now = _utcnow()
    with SessionLocal() as session:
        updated = (
            session.query(InboundJob)
            .filter_by(id=job_id, status="processing", locked_by=owner)
            .update(
                {InboundJob.locked_at: now, InboundJob.updated_at: now},
                synchronize_session=False,
            )
        )
        session.commit()
        return bool(updated)


def _lease_heartbeat(job_id: int, owner: str, stopped: threading.Event) -> None:
    while not stopped.wait(HEARTBEAT_SECONDS):
        try:
            if not _renew_job_lease(job_id, owner):
                return
        except Exception:
            logger.exception("Inbound job lease heartbeat failed (job=%d)", job_id)


def _finish_job(job_id: int, owner: str) -> None:
    now = _utcnow()
    with SessionLocal() as session:
        session.query(InboundJob).filter_by(
            id=job_id, status="processing", locked_by=owner
        ).update(
            {
                InboundJob.status: "done",
                InboundJob.completed_at: now,
                InboundJob.locked_at: None,
                InboundJob.locked_by: None,
                InboundJob.last_error: None,
                InboundJob.updated_at: now,
            },
            synchronize_session=False,
        )
        session.commit()


def _retry_job(job_id: int, owner: str, attempts: int, exc: Exception) -> None:
    now = _utcnow()
    terminal = attempts >= MAX_ATTEMPTS
    delay = min(30 * (2 ** max(0, attempts - 1)), 30 * 60)
    error = f"{type(exc).__name__}: {str(exc)[:500]}"
    with SessionLocal() as session:
        session.query(InboundJob).filter_by(
            id=job_id, status="processing", locked_by=owner
        ).update(
            {
                InboundJob.status: "dead" if terminal else "pending",
                InboundJob.available_at: now + timedelta(seconds=delay),
                InboundJob.locked_at: None,
                InboundJob.locked_by: None,
                InboundJob.last_error: error,
                InboundJob.updated_at: now,
            },
            synchronize_session=False,
        )
        session.commit()


def process_one_inbound_job() -> bool:
    """Claim and process one job. Returns false when no job is ready."""
    claimed = _claim_next_job()
    if not claimed:
        return False

    job_id, payload, attempts, owner = claimed
    ticket_id = str(payload["ticket_id"])
    heartbeat_stop = threading.Event()
    heartbeat = threading.Thread(
        target=_lease_heartbeat,
        args=(job_id, owner, heartbeat_stop),
        name=f"inbound-heartbeat-{job_id}",
        daemon=True,
    )
    heartbeat.start()
    try:
        from ..integrations.hubspot import HubSpotClient
        from .inbound import InboundAgent

        hubspot = HubSpotClient()
        contact_id = hubspot.get_ticket_primary_contact_sync(ticket_id)
        if not contact_id:
            raise RuntimeError("ticket has no associated contact yet")
        result = InboundAgent(hubspot=hubspot).handle(
            {
                "event_type": payload.get("event_type") or "ticket_created",
                "object_id": contact_id,
                "ticket_id": ticket_id,
                "occurred_at": payload.get("occurred_at"),
                # Internal recovery metadata.  InboundAgent stores the placeholder
                # id on this durable job in the same transaction that creates it,
                # so a reclaimed lease resumes that exact draft.
                "_inbound_job_id": job_id,
                "_draft_message_id": payload.get("draft_message_id"),
            }
        )
        if isinstance(result, dict) and result.get("status") == "skipped_no_body":
            raise RuntimeError("ticket body is not available yet")
    except Exception as exc:
        _retry_job(job_id, owner, attempts, exc)
        logger.exception(
            "Inbound job failed; retry scheduled (ticket=%s attempt=%d)",
            ticket_id,
            attempts,
        )
    else:
        _finish_job(job_id, owner)
        logger.info("Inbound job completed (ticket=%s)", ticket_id)
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=1)
    return True


async def run_inbound_worker() -> None:
    """Continuously drain the durable inbound queue."""
    logger.info("Durable inbound worker started")
    while True:
        try:
            from .worker_heartbeat import record_worker_heartbeat

            await asyncio.to_thread(record_worker_heartbeat, "inbound")
            handled = await asyncio.to_thread(process_one_inbound_job)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A temporary database/claim failure must not kill the application task.
            logger.exception("Inbound worker iteration failed")
            handled = False
        if not handled:
            await asyncio.sleep(IDLE_SECONDS)
