"""The two helpers the route modules still share.

Everything else here was Jinja plumbing — the templates object, `asset_url`, the `kst`
filter, the `pending_user_count` global. The console is React and fetches JSON, so the
templates are gone and so is what existed to serve them.
"""

from __future__ import annotations

import html
from urllib.parse import urlsplit

_CONTROL = ("\r", "\n", "\t")


def external_url(value: str | None) -> str:
    """Return only absolute HTTP(S) links for operator-entered artifact fields.

    A stored ``javascript:`` would run in the console the moment someone clicks the link,
    so anything not plainly http(s) comes back empty and renders as no link at all.
    """
    value = (value or "").strip()
    if any(ch in value for ch in _CONTROL):
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


def esc(text: str) -> str:
    """Minimal HTML escape for status fragments rendered into hx-swap responses."""
    return html.escape(text or "", quote=True)
