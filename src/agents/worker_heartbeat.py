"""Low-cost database heartbeats for the in-process background workers."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import select

from ..db.models import Event
from ..db.session import SessionLocal

_last_written: dict[str, float] = {}


def heartbeat_kind(worker: str) -> str:
    return f"worker_heartbeat:{worker}"


def record_worker_heartbeat(worker: str, *, min_interval_seconds: float = 30) -> None:
    """Update one row per worker, throttled to avoid a write on every idle tick."""
    now_monotonic = time.monotonic()
    if now_monotonic - _last_written.get(worker, 0) < min_interval_seconds:
        return
    now = datetime.now(timezone.utc)
    kind = heartbeat_kind(worker)
    with SessionLocal() as session:
        row = session.scalar(
            select(Event).where(Event.kind == kind).order_by(Event.id).limit(1)
        )
        if row is None:
            row = Event(kind=kind, payload={"worker": worker})
            session.add(row)
        row.created_at = now
        row.payload = {"worker": worker, "heartbeat_at": now.isoformat()}
        session.commit()
    _last_written[worker] = now_monotonic
