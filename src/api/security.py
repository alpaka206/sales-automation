"""Request-level security helpers for the API auth middleware.

Localhost gating and web-UI HTTP Basic Auth. The middleware itself lives in
``main.py``; these are the pure decision helpers it calls.
"""

from __future__ import annotations

import base64
import hmac
from urllib.parse import urlsplit

from fastapi import Request

from ..common.config import settings

# Paths the auth middleware lets through without any check.
API_SKIP_PATHS = ("/healthz", "/favicon.ico")
LOCAL_DOC_PATHS = ("/docs", "/redoc", "/openapi.json")
# Browser-facing web UI route prefixes (vs JSON API / webhooks).
WEB_UI_PREFIXES = (
    "/", "/overview", "/messages", "/email-templates", "/settings",
    "/logs", "/static", "/auth", "/tools", "/customers", "/operations",
    "/pipeline", "/companies", "/contacts", "/outbound-history",
    "/integrations",
)
LOCALHOST_HOSTS = ("127.0.0.1", "::1", "localhost")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_ADMIN_MUTATION_PREFIXES = (
    "/settings/users",
    "/email-templates",
    "/integrations",
    "/logs",
)


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

    ``APP_HOST`` only controls the bind address and is not authentication. Both
    the network peer and requested hostname must be local, preventing a reverse
    proxy or container bind from turning every external request into localhost.
    """
    ip = client_ip(request)
    host = (request.url.hostname or "").lower()
    if ip == "testclient":
        return host in {"testserver", *LOCALHOST_HOSTS}
    return ip in LOCALHOST_HOSTS and host in LOCALHOST_HOSTS


def is_same_origin_browser_request(request: Request) -> bool:
    """Reject cross-site browser writes while preserving authenticated API clients."""
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return True
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return False

    source = request.headers.get("origin")
    if source == "null":
        return False
    if not source:
        source = request.headers.get("referer")
    if not source:
        return True

    expected = settings.PUBLIC_BASE_URL or str(request.base_url)
    return _origin(source) == _origin(expected)


def web_role_allows(role: str, method: str, path: str) -> bool:
    """Two-role policy for browser routes.

    ``viewer`` is read-only; everything else (``admin``, plus the legacy
    ``operator``/``member`` values that were merged into it) has full access.
    Mirrors auth.normalize_role — keep the two in step.
    """
    if role != "viewer":
        return True
    # Viewers may read anything the UI renders, but never mutate and never reach
    # the integration-connect flows (which hand out OAuth grants).
    if path == "/integrations" or path.startswith("/integrations/"):
        return False
    return method.upper() in _SAFE_METHODS


def _origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    return parsed.scheme, parsed.hostname.lower(), port


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
