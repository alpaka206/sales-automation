"""Request-level security helpers for the API auth middleware.

Localhost gating and web-UI HTTP Basic Auth. The middleware itself lives in
``main.py``; these are the pure decision helpers it calls.
"""

from __future__ import annotations

import base64
import hmac

from fastapi import Request

from ..common.config import settings

# Paths the auth middleware lets through without any check.
API_SKIP_PATHS = ("/healthz", "/docs", "/openapi.json", "/favicon.ico")
# Browser-facing web UI route prefixes (vs JSON API / webhooks).
WEB_UI_PREFIXES = (
    "/", "/messages", "/knowledge", "/outbound", "/settings", "/icp-rules",
    "/prospects", "/unsubscribe",
)
LOCALHOST_HOSTS = ("127.0.0.1", "::1", "localhost")


def is_web_ui_path(path: str) -> bool:
    """Return True for browser-facing web UI routes (not /api, /webhook, etc.)."""
    if path == "/":
        return True
    return any(path.startswith(p) for p in WEB_UI_PREFIXES if p != "/")


def _trusted_proxies() -> set[str]:
    return {p.strip() for p in (settings.TRUSTED_PROXIES or "").split(",") if p.strip()}


def client_ip(request: Request) -> str | None:
    """Return the real client IP. X-Forwarded-For is only honored when the immediate
    peer is on TRUSTED_PROXIES — otherwise the header is attacker-controlled."""
    peer = request.client.host if request.client else None
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd and peer and peer in _trusted_proxies():
        return fwd.split(",")[0].strip() or peer
    return peer


def is_localhost(request: Request) -> bool:
    """Return True when the request originates from localhost.

    Trust model:
      1. If APP_HOST is bound to a loopback address, the OS already guarantees
         no external traffic can reach this process — every request is local.
      2. Otherwise, we inspect the real peer IP. We do NOT honor
         X-Forwarded-For unless the immediate peer is on TRUSTED_PROXIES.
         A naive `X-Forwarded-For` trust would let any external client spoof
         the header and bypass the localhost-only gate.
    """
    if settings.APP_HOST in LOCALHOST_HOSTS:
        return True
    ip = client_ip(request)
    return ip in LOCALHOST_HOSTS


def check_web_ui_basic_auth(request: Request) -> bool:
    """Validate HTTP Basic Auth against WEB_UI_USERNAME / WEB_UI_PASSWORD.

    Returns False when no password is configured (so callers fall back to the
    localhost-only gate).
    """
    pw = settings.WEB_UI_PASSWORD
    if not pw:
        return False
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except Exception:
        return False
    user, sep, passwd = decoded.partition(":")
    if not sep:
        return False
    return hmac.compare_digest(user, settings.WEB_UI_USERNAME) and hmac.compare_digest(passwd, pw)
