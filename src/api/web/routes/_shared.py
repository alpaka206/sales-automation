"""Shared helpers for the web UI route modules: Jinja templates and utilities."""

from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.templating import Jinja2Templates

# routes/ lives under web/; templates are at web/templates.
_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# All timestamps are stored as UTC (naive, via models._utcnow). The operator is in
# Korea, so render everything in KST. Centralised as a Jinja filter so every
# template formats time the same way.
_KST = timezone(timedelta(hours=9))


def kst(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a stored (UTC) datetime in Korea Standard Time. '' for None."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime(fmt)


templates.env.filters["kst"] = kst


def external_url(value: str | None) -> str:
    """Return only absolute HTTP(S) links for operator-entered artifact fields."""
    value = (value or "").strip()
    if "\r" in value or "\n" in value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    safe = (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )
    return value if safe else ""


templates.env.filters["external_url"] = external_url


def pending_user_count() -> int:
    """Number of users awaiting access approval — drives the nav 'approve' badge.

    Returns 0 outside Google-OAuth mode or on any error, so a DB hiccup never
    breaks page rendering. Exposed to templates as a Jinja global below.
    """
    from ....common.config import settings

    if settings.AUTH_MODE != "google_oauth":
        return 0
    try:
        from sqlalchemy import func, select

        from ....db.models import User
        from ....db.session import SessionLocal

        with SessionLocal() as session:
            return (
                session.scalar(
                    select(func.count()).select_from(User).where(User.approved.is_(False))
                )
                or 0
            )
    except Exception:
        return 0


# Make the count callable from any template (the nav partial is shared by all pages).
templates.env.globals["pending_user_count"] = pending_user_count

# Statuses surfaced on the dashboard's status-count widget.
TRACKED_STATUSES = (
    "drafting",
    "pending_approval",
    "approved",
    "sent",
    "draft_failed",
    "send_failed",
    "delivery_unknown",
    "rejected",
)


def esc(text: str) -> str:
    """Minimal HTML escape for status fragments rendered into hx-swap responses."""
    return html.escape(text or "", quote=True)
