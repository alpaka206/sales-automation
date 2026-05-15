"""Background worker that polls for approved messages and sends them with rate limiting."""

from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from datetime import datetime, timezone

from ..common.config import settings
from ..db.models import Conversation, Message
from ..db.session import SessionLocal
from .outbound.status import ProspectStatus, transition, InvalidStatusTransition

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60

_sent_timestamps: deque[float] = deque()
_daily_count: int = 0
_daily_date: str = ""


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


async def _send_one(message_id: int) -> bool:
    """Send a single message and update its status. Returns True if sent."""
    from ..integrations.senders import send

    session = SessionLocal()
    try:
        msg = session.get(Message, message_id)
        if not msg or msg.status != "approved":
            return False

        try:
            await send(msg)
            msg.status = "sent"
            msg.sent_at = datetime.now(timezone.utc)

            conv = session.get(Conversation, msg.conversation_id)
            if conv and conv.prospect_id:
                try:
                    transition(session, conv.prospect_id, ProspectStatus.SENT, reason="send_worker")
                except (InvalidStatusTransition, ValueError):
                    pass

            session.commit()
            _record_send()
            logger.info("Worker sent message %d.", message_id)
            return True
        except Exception as exc:
            session.rollback()
            msg = session.get(Message, message_id)
            if msg:
                msg.status = "send_failed"
                session.commit()
            logger.error("Worker failed to send message %d: %s", message_id, exc)
            return False
    finally:
        session.close()


def _pick_ready_ids() -> list[int]:
    """Return IDs of approved messages whose scheduled_at has passed."""
    now = datetime.now(timezone.utc)
    session = SessionLocal()
    try:
        rows = (
            session.query(Message.id)
            .filter(
                Message.status == "approved",
                (Message.scheduled_at <= now) | (Message.scheduled_at.is_(None)),
            )
            .order_by(Message.scheduled_at.asc().nullsfirst())
            .limit(50)
            .all()
        )
        return [r[0] for r in rows]
    finally:
        session.close()


async def run_send_worker() -> None:
    """Poll loop with rate limiting, daily cap, and jitter."""
    logger.info(
        "Send worker started (poll %ds, rate %d/min, daily cap %d, jitter %ds).",
        POLL_INTERVAL_SECONDS,
        settings.SEND_RATE_PER_MINUTE,
        settings.DAILY_SEND_LIMIT,
        settings.SEND_JITTER_SECONDS,
    )
    while True:
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

            ids = _pick_ready_ids()
            if ids:
                logger.info("Send worker found %d ready message(s).", len(ids))

            for mid in ids:
                if _daily_limit_reached():
                    logger.info("Daily limit hit mid-batch. Stopping.")
                    break

                if _minute_window_full():
                    logger.info("Minute rate limit hit. Waiting 60s.")
                    await asyncio.sleep(60)

                if settings.SEND_JITTER_SECONDS > 0:
                    jitter = random.uniform(0, settings.SEND_JITTER_SECONDS)
                    await asyncio.sleep(jitter)

                await _send_one(mid)

        except Exception:
            logger.exception("Send worker tick error.")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
