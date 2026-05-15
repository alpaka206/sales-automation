"""Send-time scheduler based on per-country optimal windows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

from ..db.models import CountrySendWindow
from ..db.session import SessionLocal


def _get_window(country_code: str) -> CountrySendWindow | None:
    """Fetch the send window for a country code, falling back to 'default'."""
    session = SessionLocal()
    try:
        row = session.query(CountrySendWindow).filter_by(
            country_code=country_code.upper()
        ).first()
        if row:
            return row
        return session.query(CountrySendWindow).filter_by(country_code="default").first()
    finally:
        session.close()


def compute_next_send_time(
    country_code: str, now_utc: datetime | None = None
) -> datetime:
    """Return the next optimal send time (UTC) for a given country."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    window = _get_window(country_code)
    if window is None:
        return now_utc

    tz = ZoneInfo(window.timezone)
    local_now = now_utc.astimezone(tz)
    avoid = set(window.avoid_days_of_week or [])

    candidate = local_now.replace(
        hour=window.hours_start, minute=0, second=0, microsecond=0
    )

    if local_now.hour >= window.hours_end:
        candidate += timedelta(days=1)
    elif local_now.hour >= window.hours_start:
        candidate = local_now

    for _ in range(8):
        if candidate.weekday() not in avoid:
            break
        candidate = candidate.replace(
            hour=window.hours_start, minute=0, second=0, microsecond=0
        )
        candidate += timedelta(days=1)

    return candidate.astimezone(timezone.utc)
