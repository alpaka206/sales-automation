"""Shared helpers for the web UI route modules: Jinja templates and utilities."""

from __future__ import annotations

import html
from pathlib import Path

from fastapi.templating import Jinja2Templates

# routes/ lives under web/; templates are at web/templates.
_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


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
            return session.scalar(
                select(func.count()).select_from(User).where(User.approved.is_(False))
            ) or 0
    except Exception:
        return 0


# Make the count callable from any template (the nav partial is shared by all pages).
templates.env.globals["pending_user_count"] = pending_user_count

# Statuses surfaced on the dashboard's status-count widget.
TRACKED_STATUSES = ("pending_approval", "approved", "sent", "bounced", "replied")


def esc(text: str) -> str:
    """Minimal HTML escape for status fragments rendered into hx-swap responses."""
    return html.escape(text or "", quote=True)
