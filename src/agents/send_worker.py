"""Background worker that polls for approved messages and sends them with rate limiting."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections import deque
from datetime import datetime, timezone

from sqlalchemy import update

from ..common.config import settings
from ..db.models import Contact, Conversation, Message
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
    return _daily_count >= settings.DAILY_SEND_LIMIT


def _minute_window_full() -> bool:
    """Check if per-minute rate limit is reached."""
    now = asyncio.get_event_loop().time()
    while _sent_timestamps and now - _sent_timestamps[0] > 60:
        _sent_timestamps.popleft()
    return len(_sent_timestamps) >= settings.SEND_RATE_PER_MINUTE


def _record_send() -> None:
    """Record a successful send for rate tracking."""
    global _daily_count
    _sent_timestamps.append(asyncio.get_event_loop().time())
    _daily_count += 1


def get_daily_count() -> int:
    """Return current daily send count (for healthcheck)."""
    _reset_daily_if_needed()
    return _daily_count


SEND_TRANSIENT_MAX_RETRIES = 3


async def _post_send_bookkeeping(session, msg, conv, message_id: int) -> None:
    """Best-effort HubSpot side effects after a successful send. Never raises —
    the email already went out, so failures here must not reverse it."""
    from ..integrations.hubspot import HubSpotClient, move_ticket_stage_after_send

    ticket_id = conv.hubspot_ticket_id if conv else None
    if ticket_id:
        await asyncio.to_thread(move_ticket_stage_after_send, ticket_id)

    hubspot_contact_id = None
    if conv and conv.contact_id:
        contact = session.get(Contact, conv.contact_id)
        hubspot_contact_id = contact.hubspot_contact_id if contact else None
    if hubspot_contact_id:
        try:
            await HubSpotClient().create_email_engagement(
                contact_id=hubspot_contact_id,
                subject=msg.subject or "",
                body=msg.body or "",
            )
            logger.info(
                "Logged HubSpot engagement for contact %s (msg %d).",
                hubspot_contact_id, message_id,
            )
        except Exception:
            logger.exception(
                "HubSpot engagement log failed (contact=%s, msg=%d). Send succeeded.",
                hubspot_contact_id, message_id,
            )


async def _send_one(message_id: int) -> bool:
    """Send a single message that this worker has already claimed (status == _WORKER_ID).

    Caller is responsible for the atomic claim. We only send + update.
    Transient failures (network blip, 421/451) are retried with exponential backoff
    inside this call. Permanent failures (bad recipient, auth) fail immediately to
    send_failed without retry.
    """
    from ..integrations.senders import send
    from ..integrations.senders.smtp import SMTPPermanentError, SMTPTransientError

    session = SessionLocal()
    try:
        msg = session.get(Message, message_id)
        if not msg or msg.status != _WORKER_ID:
            # Lost the row (shouldn't happen — caller already claimed) or another process intervened.
            return False

        last_exc: Exception | None = None
        for attempt in range(SEND_TRANSIENT_MAX_RETRIES):
            try:
                await send(msg)
                msg.status = "sent"
                msg.sent_at = datetime.now(timezone.utc)

                conv = session.get(Conversation, msg.conversation_id)
                session.commit()
                _record_send()

                await _post_send_bookkeeping(session, msg, conv, message_id)
                logger.info("Worker sent message %d.", message_id)
                return True
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
            msg.status = "send_failed"
            session.commit()
        logger.error("Worker failed to send message %d: %s", message_id, last_exc)
        return False
    finally:
        session.close()


def _claim_ready_id() -> int | None:
    """Atomically claim ONE approved message whose scheduled_at has passed.

    Strategy: SELECT a candidate id, then UPDATE…WHERE id=:id AND status='approved'.
    Only one worker's UPDATE will affect a row; losers see rowcount==0 and retry.
    This works on SQLite (with WAL) and Postgres without needing FOR UPDATE.
    """
    now = datetime.now(timezone.utc)
    session = SessionLocal()
    try:
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
            result = session.execute(
                update(Message)
                .where(Message.id == mid, Message.status == "approved")
                .values(status=_WORKER_ID)
            )
            session.commit()
            if result.rowcount == 1:
                return mid
            # rowcount==0 → another worker took it; try next candidate.

        return None
    finally:
        session.close()


def request_shutdown() -> None:
    """Signal the send worker to exit at the next checkpoint."""
    global _shutdown
    _shutdown = True


def _reclaim_stuck_sending() -> int:
    """Reset rows stuck in `sending:*` to `approved` so they're retried.

    A row gets stuck if the worker crashed between claim and the final commit.
    Called once at startup. Safe under multiple workers (idempotent UPDATE).
    """
    session = SessionLocal()
    try:
        result = session.execute(
            update(Message)
            .where(Message.status.like("sending:%"))
            .values(status="approved")
        )
        session.commit()
        return result.rowcount or 0
    finally:
        session.close()


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

    reclaimed = _reclaim_stuck_sending()
    if reclaimed:
        logger.info("Reclaimed %d message(s) stuck in 'sending:*' state.", reclaimed)

    while not _shutdown:
        try:
            _reset_daily_if_needed()

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
