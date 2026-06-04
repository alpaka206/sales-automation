"""Shared helpers for the web UI route modules: Jinja templates and utilities."""

from __future__ import annotations

import html
from pathlib import Path

from fastapi.templating import Jinja2Templates

# routes/ lives under web/; templates are at web/templates.
_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# Statuses surfaced on the dashboard's status-count widget.
TRACKED_STATUSES = ("pending_approval", "approved", "sent", "bounced", "replied")


def esc(text: str) -> str:
    """Minimal HTML escape for status fragments rendered into hx-swap responses."""
    return html.escape(text or "", quote=True)
