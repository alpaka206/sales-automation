"""Background worker that polls for approved messages and sends them with rate limiting."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections import deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from ..common.config import settings
from ..db.conversation_history import add_progress
from ..db.models import Contact, Conversation, CustomerProfile, Message
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60

# Unique per-process token used as the value of Message.status while a worker holds the row.
# Format: "sending:<pid>:<random>". This makes the atomic UPDATE…WHERE status='approved'
# act as a row-level lock — the loser sees zero affected rows and moves on.
_WORKER_ID = f"sending:{os.getpid()}:{random.randint(1_000_000, 9_999_999)}"

_sent_timestamps: deque[float] = deque()
_daily_count: int = 0
_daily_date: str = ""
_shutdown = False


def _reset_daily_if_needed(now: datetime | None = None) -> None:
    """Reset daily counter at midnight UTC."""
    global _daily_count, _daily_date
    today = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    if today != _daily_date:
        _daily_date = today
        _daily_count = 0


def _daily_limit_reached() -> bool:
    """Check if daily send limit has been reached."""
    if settings.DAILY_SEND_LIMIT <= 0:
        return False
    daily, _minute = _database_send_counts()
    return max(_daily_count, daily) >= settings.DAILY_SEND_LIMIT


def _minute_window_full() -> bool:
    """Check if per-minute rate limit is reached."""
    if settings.SEND_RATE_PER_MINUTE <= 0:
        return False
    now = asyncio.get_event_loop().time()
    while _sent_timestamps and now - _sent_timestamps[0] > 60:
        _sent_timestamps.popleft()
    _daily, minute = _database_send_counts()
    return max(len(_sent_timestamps), minute) >= settings.SEND_RATE_PER_MINUTE


def _record_send() -> None:
    """Record a successful send for rate tracking."""
    global _daily_count
    _sent_timestamps.append(asyncio.get_event_loop().time())
    _daily_count += 1


def get_daily_count() -> int:
    """Return current daily send count (for healthcheck)."""
    _reset_daily_if_needed()
    daily, _minute = _database_send_counts()
    return max(_daily_count, daily)


def _database_send_counts(now: datetime | None = None) -> tuple[int, int]:
    """Persist rate accounting across restarts and the current process."""
    current = now or datetime.now(timezone.utc)
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    minute_start = current - timedelta(seconds=60)
    try:
        with SessionLocal() as session:
            daily = session.scalar(
                select(func.count(Message.id)).where(Message.sent_at >= day_start)
            ) or 0
            minute = session.scalar(
                select(func.count(Message.id)).where(Message.sent_at >= minute_start)
            ) or 0
        return int(daily), int(minute)
    except Exception:
        logger.warning("Could not read persisted send quota; using process counters.", exc_info=True)
        return 0, 0


SEND_TRANSIENT_MAX_RETRIES = 3
SEND_LEASE_SECONDS = 15 * 60
POST_SEND_SYNC_MAX_RETRIES = 8
AUTO_ACK_QUEUE_MAX_ATTEMPTS = 5


async def _post_send_bookkeeping(session, msg, conv, message_id: int) -> None:
    """Best-effort HubSpot side effects after a successful send. Never raises —
    the email already went out, so failures here must not reverse it."""
    from ..integrations.google_sheets import is_configured as sheets_configured
    from ..integrations.google_sheets import update_inbound_stage
    from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured
    from ..integrations.hubspot import move_ticket_stage_after_send

    errors: list[str] = []
    ticket_id = conv.hubspot_ticket_id if conv else None
    if ticket_id and not await asyncio.to_thread(move_ticket_stage_after_send, ticket_id):
        errors.append("hubspot_ticket_stage")

    hubspot_contact_id = None
    contact = None
    if conv and conv.contact_id:
        contact = session.get(Contact, conv.contact_id)
        hubspot_contact_id = contact.hubspot_contact_id if contact else None
    if hubspot_contact_id and settings.HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS:
        client = None
        try:
            client = HubSpotClient()
            await client.update_inbound_status(hubspot_contact_id, "meeting_link_sent")
        except HubSpotNotConfigured:
            errors.append("hubspot_contact_status:not_configured")
        except Exception as exc:
            errors.append(f"hubspot_contact_status:{type(exc).__name__}")
            logger.warning(
                "HubSpot contact status update failed (contact=%s, msg=%d). Send succeeded.",
                hubspot_contact_id, message_id,
                exc_info=True,
            )
        finally:
            if client is not None:
                await client.close()

    if conv:
        profile = session.get(CustomerProfile, conv.contact_id)
        if profile:
            profile.pipeline_stage = "meeting_link_sent"
        sheet_client_id = conv.sheet_client_id or (contact.sheet_client_id if contact else None)
        if not sheets_configured():
            errors.append("google_sheets:not_configured")
        elif not isinstance(sheet_client_id, int) or sheet_client_id <= 0:
            errors.append("google_sheets:missing_client_id")
        else:
            sheet_ok = await asyncio.to_thread(
                update_inbound_stage,
                sheet_client_id,
                "meeting_link_sent",
                profile.qualification if profile else None,
            )
            if not sheet_ok:
                errors.append("google_sheets_stage")

        now = datetime.now(timezone.utc)
        previous_attempts = msg.post_send_sync_attempts
        previous_attempts = previous_attempts if isinstance(previous_attempts, int) else 0
        msg.post_send_sync_attempted_at = now
        msg.post_send_sync_attempts = previous_attempts + 1
        msg.post_send_sync_error = ", ".join(errors)[:1000] or None
        msg.post_send_synced_at = None if errors else now
        session.commit()
        if msg.post_send_sync_attempts > 1:
            return
        add_progress(conv.id, "reply", f"답변 발송 완료: {msg.subject or '(제목 없음)'}"[:200])


async def _send_one(message_id: int) -> bool:
    """Send a single message that this worker has already claimed (status == _WORKER_ID).

    Caller is responsible for the atomic claim. We only send + update.
    Transient failures (network blip, 421/451) are retried with exponential backoff
    inside this call. Permanent failures (bad recipient, auth) fail immediately to
    send_failed without retry.
    """
    from ..integrations.senders import send
    from ..integrations.senders.smtp import (
        SMTPDeliveryUnknown,
        SMTPPermanentError,
        SMTPTransientError,
    )

    session = SessionLocal()
    try:
        msg = session.get(Message, message_id)
        if not msg or msg.status != _WORKER_ID:
            # Lost the row (shouldn't happen — caller already claimed) or another process intervened.
            return False

        if msg.channel == "email" and not msg.smtp_message_id:
            from ..integrations.senders.smtp import _generate_message_id

            # Persist the provider reconciliation key before touching SMTP.
            msg.smtp_message_id = _generate_message_id(message_id)
            session.commit()

        last_exc: Exception | None = None
        for attempt in range(SEND_TRANSIENT_MAX_RETRIES):
            try:
                await send(msg)
                from ..common.safe_mode import resolve_send_override

                # Non-empty in safe mode (forced ronald@…) → status "test_sent" and
                # post-send HubSpot/Sheets bookkeeping is skipped, same as an
                # explicit SEND_OVERRIDE_EMAIL.
                test_mode = bool(resolve_send_override())
                msg.status = "test_sent" if test_mode else "sent"
                msg.send_claimed_at = None
                msg.sent_at = datetime.now(timezone.utc)

                # Local bookkeeping records OUR activity and is always written, including
                # in safe mode. Gating it on `not test_mode` used to leave last_outgoing_at
                # and stage permanently unset pre-launch (safe mode always forces an
                # override address), which silently emptied every "no reply for N days"
                # and pipeline view. External side effects stay guarded — the HubSpot /
                # Sheets writes below still run only when `not test_mode`.
                conv = session.get(Conversation, msg.conversation_id)
                if conv:
                    conv.last_outgoing_at = msg.sent_at
                    if msg.prompt_variant != "auto_ack":
                        conv.stage = "meeting_link_sent"
                if msg.prompt_variant == "auto_ack" or test_mode:
                    msg.post_send_synced_at = msg.sent_at
                session.commit()
                _record_send()

                if msg.prompt_variant != "auto_ack" and not test_mode:
                    try:
                        await _post_send_bookkeeping(session, msg, conv, message_id)
                    except Exception:
                        # Delivery is already committed. A bookkeeping outage must
                        # never turn a delivered email into `send_failed`.
                        logger.exception(
                            "Post-send bookkeeping failed for message %d; queued for retry.",
                            message_id,
                        )
                logger.info("Worker sent message %d.", message_id)
                return True
            except SMTPDeliveryUnknown as exc:
                session.rollback()
                msg = session.get(Message, message_id)
                if msg:
                    msg.status = "delivery_unknown"
                    msg.send_claimed_at = None
                    session.commit()
                logger.error("SMTP delivery outcome unknown for message %d: %s", message_id, exc)
                return False
            except SMTPPermanentError as exc:
                last_exc = exc
                logger.error("Permanent send failure for message %d: %s", message_id, exc)
                break  # do not retry
            except SMTPTransientError as exc:
                last_exc = exc
                if attempt < SEND_TRANSIENT_MAX_RETRIES - 1:
                    delay = 2 ** attempt
                    logger.warning(
                        "Transient send failure for message %d (attempt %d/%d): %s — retry in %ds",
                        message_id, attempt + 1, SEND_TRANSIENT_MAX_RETRIES, exc, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error("Transient send failure exhausted retries for message %d: %s", message_id, exc)
                break
            except Exception as exc:
                # Unknown error class — treat as permanent to avoid infinite retries.
                last_exc = exc
                logger.error("Unknown send failure for message %d: %s", message_id, exc)
                break

        # All retries exhausted or hit permanent error.
        session.rollback()
        msg = session.get(Message, message_id)
        if msg:
            retry_auto_ack = (
                isinstance(last_exc, SMTPTransientError)
                and msg.prompt_variant == "auto_ack"
                and msg.send_attempts < AUTO_ACK_QUEUE_MAX_ATTEMPTS
            )
            msg.status = "approved" if retry_auto_ack else "send_failed"
            msg.send_claimed_at = None
            if retry_auto_ack:
                delay = min(60 * (2 ** max(msg.send_attempts - 1, 0)), 15 * 60)
                msg.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            session.commit()
            if retry_auto_ack:
                logger.warning(
                    "Auto-ack %d requeued after transient SMTP failure (attempt %d/%d).",
                    message_id,
                    msg.send_attempts,
                    AUTO_ACK_QUEUE_MAX_ATTEMPTS,
                )
                return False
        logger.error("Worker failed to send message %d: %s", message_id, last_exc)
        return False
    finally:
        session.close()


def _claim_id(message_id: int) -> bool:
    """Atomically claim one specific approved message."""
    with SessionLocal() as session:
        result = session.execute(
            update(Message)
            .where(Message.id == message_id, Message.status == "approved")
            .values(
                status=_WORKER_ID,
                send_claimed_at=datetime.now(timezone.utc),
                send_attempts=Message.send_attempts + 1,
            )
        )
        session.commit()
        return result.rowcount == 1


def _claim_ready_id() -> int | None:
    """Atomically claim ONE approved message whose scheduled_at has passed.

    Strategy: SELECT a candidate id, then UPDATE…WHERE id=:id AND status='approved'.
    Only one worker's UPDATE will affect a row; losers see rowcount==0 and retry.
    This works on SQLite (with WAL) and Postgres without needing FOR UPDATE.
    """
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        candidate_ids = (
            session.query(Message.id)
            .filter(
                Message.status == "approved",
                (Message.scheduled_at <= now) | (Message.scheduled_at.is_(None)),
            )
            .order_by(Message.scheduled_at.asc().nullsfirst())
            .limit(50)
            .all()
        )

    for (mid,) in candidate_ids:
        if _claim_id(mid):
            return mid
        # Another worker took it; try the next candidate.

    return None


async def send_approved_now(message_id: int) -> bool:
    """Use the same atomic claim and delivery path as the background worker."""
    _reset_daily_if_needed()
    if _daily_limit_reached() or _minute_window_full():
        with SessionLocal() as session:
            session.execute(
                update(Message)
                .where(Message.id == message_id, Message.status == "approved")
                .values(scheduled_at=datetime.now(timezone.utc) + timedelta(seconds=60))
            )
            session.commit()
        logger.warning("Message %d deferred by the shared send quota.", message_id)
        return False
    if not _claim_id(message_id):
        return False
    return await _send_one(message_id)


def request_shutdown() -> None:
    """Signal the send worker to exit at the next checkpoint."""
    global _shutdown
    _shutdown = True


def _reclaim_stuck_sending(now: datetime | None = None) -> int:
    """Quarantine stale sends whose delivery outcome cannot be known safely.

    SMTP may have accepted the message just before the worker crashed. Automatic
    replay could duplicate a customer email, so an operator must verify it first.
    """
    session = SessionLocal()
    try:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=SEND_LEASE_SECONDS)
        result = session.execute(
            update(Message)
            .where(
                Message.status.like("sending:%"),
                Message.send_claimed_at.is_not(None),
                Message.send_claimed_at <= cutoff,
            )
            .values(status="delivery_unknown", send_claimed_at=None)
        )
        session.commit()
        return result.rowcount or 0
    finally:
        session.close()


def _sync_retry_due(msg: Message, now: datetime) -> bool:
    attempted_at = msg.post_send_sync_attempted_at
    if attempted_at is None:
        return True
    if attempted_at.tzinfo is None:
        attempted_at = attempted_at.replace(tzinfo=timezone.utc)
    delay = min(3600, 60 * (2 ** max((msg.post_send_sync_attempts or 1) - 1, 0)))
    return attempted_at <= now - timedelta(seconds=delay)


async def _retry_post_send_syncs(limit: int = 20) -> int:
    """Retry CRM/Sheets updates without ever resending the delivered email."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        candidates = (
            session.query(Message.id)
            .filter(
                Message.status == "sent",
                Message.post_send_synced_at.is_(None),
                Message.post_send_sync_attempts < POST_SEND_SYNC_MAX_RETRIES,
            )
            .order_by(Message.post_send_sync_attempted_at.asc().nullsfirst())
            .limit(limit)
            .all()
        )

    retried = 0
    for (message_id,) in candidates:
        with SessionLocal() as session:
            msg = session.get(Message, message_id)
            if not msg or not _sync_retry_due(msg, now):
                continue
            conv = session.get(Conversation, msg.conversation_id)
            try:
                await _post_send_bookkeeping(session, msg, conv, message_id)
                retried += 1
            except Exception:
                session.rollback()
                logger.exception("Post-send sync retry failed for message %d.", message_id)
    return retried


async def run_send_worker() -> None:
    """Poll loop with rate limiting, daily cap, jitter, and graceful shutdown.

    Atomic claim makes the loop safe under multiple workers/processes — each row is
    sent by exactly one process.
    """
    logger.info(
        "Send worker started (id=%s, poll %ds, rate %d/min, daily cap %d, jitter %ds).",
        _WORKER_ID,
        POLL_INTERVAL_SECONDS,
        settings.SEND_RATE_PER_MINUTE,
        settings.DAILY_SEND_LIMIT,
        settings.SEND_JITTER_SECONDS,
    )

    while not _shutdown:
        try:
            from .worker_heartbeat import record_worker_heartbeat

            await asyncio.to_thread(record_worker_heartbeat, "send")
            _reset_daily_if_needed()
            quarantined = _reclaim_stuck_sending()
            if quarantined:
                logger.warning(
                    "Quarantined %d stale send(s) as delivery_unknown.", quarantined
                )
            await _retry_post_send_syncs()
            from ._notify import retry_pending_approval_notifications

            await asyncio.to_thread(retry_pending_approval_notifications)

            if _daily_limit_reached():
                logger.info(
                    "Daily send limit reached (%d/%d). Pausing until tomorrow.",
                    _daily_count,
                    settings.DAILY_SEND_LIMIT,
                )
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            sent_this_tick = 0
            while not _shutdown:
                if _daily_limit_reached():
                    break
                if _minute_window_full():
                    logger.info("Minute rate limit hit. Waiting 60s.")
                    await asyncio.sleep(60)

                mid = _claim_ready_id()
                if mid is None:
                    break

                if settings.SEND_JITTER_SECONDS > 0:
                    jitter = random.uniform(0, settings.SEND_JITTER_SECONDS)
                    await asyncio.sleep(jitter)

                await _send_one(mid)
                sent_this_tick += 1

            if sent_this_tick:
                logger.info("Send worker tick: dispatched %d message(s).", sent_this_tick)

        except Exception:
            logger.exception("Send worker tick error.")

        # Sleep in short slices so shutdown is responsive.
        for _ in range(POLL_INTERVAL_SECONDS):
            if _shutdown:
                break
            await asyncio.sleep(1)

    logger.info("Send worker shutdown complete.")
