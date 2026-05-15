"""Background worker that polls for approved messages and sends them when scheduled."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ..db.models import Message
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60


async def _send_one(message_id: int) -> None:
    """Send a single message and update its status."""
    from ..integrations.senders import send

    session = SessionLocal()
    try:
        msg = session.get(Message, message_id)
        if not msg or msg.status != "approved":
            return

        try:
            await send(msg)
            msg.status = "sent"
            msg.sent_at = datetime.now(timezone.utc)
            session.commit()
            logger.info("Worker sent message %d.", message_id)
        except Exception as exc:
            session.rollback()
            msg = session.get(Message, message_id)
            if msg:
                msg.status = "send_failed"
                session.commit()
            logger.error("Worker failed to send message %d: %s", message_id, exc)
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
    """Poll loop — runs forever in the background."""
    logger.info("Send worker started (poll every %ds).", POLL_INTERVAL_SECONDS)
    while True:
        try:
            ids = _pick_ready_ids()
            if ids:
                logger.info("Send worker found %d ready message(s).", len(ids))
            for mid in ids:
                await _send_one(mid)
        except Exception:
            logger.exception("Send worker tick error.")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
