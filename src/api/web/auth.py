"""Google OAuth sign-in for the web UI, restricted to a company domain + allowlist.

No heavy deps: the OAuth code exchange uses httpx, the ID token is verified with
``google.oauth2.id_token`` (already installed via google-genai), and the session is a
stdlib-HMAC-signed cookie (no Authlib / itsdangerous / server-side session store).

Gate (enforced in :func:`oauth_callback`):
  1. Google ID token signature/aud/exp verified by Google's library.
  2. ``email_verified`` is true AND the email is on ``ALLOWED_EMAIL_DOMAIN``.
  3. The email is approved — a bootstrap admin (``WEB_UI_ADMIN_EMAILS``) or an existing
     ``users`` row with ``approved=True``. Everyone else lands on a "pending approval"
     page until an admin approves them in the UI.

The signed session cookie carries {email, name, role, exp}; ``current_user`` also
checks the current database row so revocation and role changes take effect immediately.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from ...common.config import settings
from ...db.models import User
from ...db.session import SessionLocal

logger = logging.getLogger(__name__)


def _templates():
    # Lazy import: the routes package imports actor_name from this module, so importing
    # routes._shared at module load would create a circular import.
    from .routes._shared import templates
    return templates

router = APIRouter(tags=["auth"])

SESSION_COOKIE = "perso_session"
STATE_COOKIE = "perso_oauth_state"
SESSION_TTL = 7 * 24 * 3600  # 7 days
STATE_TTL = 600  # 10 minutes

_GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


def normalize_role(role: str | None) -> str:
    """Map legacy/unknown roles to the least-surprising effective permission."""
    if role == "admin":
        return "admin"
    if role == "viewer":
        return "viewer"
    return "operator"  # legacy "member" keeps its existing operational access


# --------------------------------------------------------------------------- #
# Signed-cookie helpers (stdlib HMAC)
# --------------------------------------------------------------------------- #
def _secret() -> bytes:
    key = settings.SESSION_SECRET
    if not key:
        raise RuntimeError("SESSION_SECRET is required when AUTH_MODE=google_oauth.")
    return key.encode("utf-8")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: dict) -> str:
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64e(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def _unsign(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        expected = _b64e(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def make_session(email: str, name: str | None, role: str) -> str:
    return _sign(
        {
            "email": email,
            "name": name or email,
            "role": normalize_role(role),
            "exp": int(time.time()) + SESSION_TTL,
        }
    )


def current_user(request: Request) -> dict | None:
    """Return the currently approved DB user for a valid signed cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    payload = _unsign(token)
    email = payload.get("email", "").lower() if payload else ""
    if not email:
        return None
    try:
        with SessionLocal() as session:
            user = session.get(User, email)
            if not user or not user.approved:
                return None
            return {
                "email": user.email,
                "name": user.name or user.email,
                "role": normalize_role(user.role),
                "exp": payload["exp"],
            }
    except Exception:
        logger.exception("Session user lookup failed.")
        return None


def session_user(request: Request) -> dict | None:
    """The logged-in user for this request (middleware-injected, else cookie)."""
    return getattr(request.state, "user", None) or current_user(request)


def actor_name(request: Request, fallback: str = "") -> str:
    """Display name to attribute an edit/approval to (logged-in user, else fallback)."""
    u = session_user(request)
    return (u.get("name") or u.get("email")) if u else fallback


def is_admin(request: Request) -> bool:
    u = session_user(request)
    return bool(u and u.get("role") == "admin")


def _cookie_secure(request: Request) -> bool:
    # PUBLIC_BASE_URL is operator-controlled; arbitrary forwarded headers are not.
    return request.url.scheme == "https" or settings.PUBLIC_BASE_URL.startswith("https://")


def _set_session(resp: Response, request: Request, token: str) -> None:
    resp.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True,
        secure=_cookie_secure(request), samesite="lax", path="/",
    )


def clear_session(resp: Response) -> None:
    resp.delete_cookie(SESSION_COOKIE, path="/")


# --------------------------------------------------------------------------- #
# Allowlist
# --------------------------------------------------------------------------- #
def _email_set(raw: str) -> set[str]:
    return {e.strip().lower() for e in (raw or "").split(",") if e.strip()}


def _admin_emails() -> set[str]:
    return _email_set(settings.WEB_UI_ADMIN_EMAILS)


def _domain_ok(email: str) -> bool:
    dom = (settings.ALLOWED_EMAIL_DOMAIN or "").lower().strip()
    return bool(dom) and email.lower().endswith("@" + dom)


def _login_or_pending(email: str, name: str | None, picture: str | None) -> tuple[dict, bool]:
    """Upsert the user on login and return ({email,name,role}, approved)."""
    email = email.lower()
    is_admin = email in _admin_emails()
    with SessionLocal() as session:
        user = session.get(User, email)
        if not user:
            user = User(email=email, approved=is_admin, role="admin" if is_admin else "operator")
            session.add(user)
        if name:
            user.name = name
        if picture:
            user.picture = picture
        if is_admin:
            user.role = "admin"
            user.approved = True
        user.last_login_at = datetime.now(timezone.utc)
        session.commit()
        return (
            {
                "email": user.email,
                "name": user.name or user.email,
                "role": normalize_role(user.role),
            },
            bool(user.approved),
        )


# --------------------------------------------------------------------------- #
# OAuth routes
# --------------------------------------------------------------------------- #
def _base_url(request: Request) -> str:
    return (settings.PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")


def _redirect_uri(request: Request) -> str:
    return _base_url(request) + "/auth/callback"


def _oauth_ready() -> bool:
    """True only when Google sign-in is fully usable end to end.

    The consent redirect signs the OAuth ``state`` with SESSION_SECRET, so a
    client id alone is not enough — without the secret (allowed in basic mode,
    where the startup guard doesn't require it) the button would render but the
    signing step raises. Gate the button and the route on the same full check.
    """
    return bool(
        settings.GOOGLE_OAUTH_CLIENT_ID
        and settings.GOOGLE_OAUTH_CLIENT_SECRET
        and settings.SESSION_SECRET
    )


@router.get("/auth/login")
async def login_page(request: Request):
    """Landing page with a 'Sign in with Google' button."""
    if current_user(request):
        return RedirectResponse("/", status_code=302)
    return _templates().TemplateResponse(
        request, "auth/login.html",
        {"domain": settings.ALLOWED_EMAIL_DOMAIN, "configured": _oauth_ready()},
    )


@router.get("/auth/google")
async def auth_google(request: Request):
    """Redirect to Google's consent screen (restricted to the company domain)."""
    if not _oauth_ready():
        return HTMLResponse("Google OAuth is not configured.", status_code=503)
    state = _sign({"n": secrets.token_urlsafe(16), "exp": int(time.time()) + STATE_TTL})
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": _redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "hd": settings.ALLOWED_EMAIL_DOMAIN,
        "prompt": "select_account",
    }
    resp = RedirectResponse(f"{_GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}", status_code=302)
    resp.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL, httponly=True,
                    secure=_cookie_secure(request), samesite="lax", path="/")
    return resp


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Exchange the code, verify the ID token, enforce domain + allowlist, set the session."""
    if error:
        return _deny(request, f"Google 로그인 취소/오류: {error}")
    cookie_state = request.cookies.get(STATE_COOKIE, "")
    if not code or not state or state != cookie_state or _unsign(state) is None:
        return _deny(request, "잘못된 로그인 요청입니다 (state 불일치). 다시 시도하세요.")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            tok = await client.post(_GOOGLE_TOKEN_ENDPOINT, data={
                "code": code,
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": _redirect_uri(request),
                "grant_type": "authorization_code",
            })
        tok.raise_for_status()
        id_tok = tok.json().get("id_token")
        claims = google_id_token.verify_oauth2_token(
            id_tok, google_requests.Request(), settings.GOOGLE_OAUTH_CLIENT_ID
        )
    except Exception:
        logger.exception("OAuth token exchange/verification failed.")
        return _deny(request, "로그인 검증에 실패했습니다. 다시 시도하세요.")

    email = (claims.get("email") or "").lower()
    if not claims.get("email_verified") or not _domain_ok(email):
        return _deny(
            request,
            f"{settings.ALLOWED_EMAIL_DOMAIN} 계정으로만 로그인할 수 있습니다. (시도한 계정: {email or '알 수 없음'})",
        )

    user, approved = _login_or_pending(email, claims.get("name"), claims.get("picture"))
    if not approved:
        return _templates().TemplateResponse(
            request, "auth/pending.html", {"email": email}, status_code=403,
        )

    resp = RedirectResponse("/", status_code=302)
    _set_session(resp, request, make_session(user["email"], user["name"], user["role"]))
    resp.delete_cookie(STATE_COOKIE, path="/")
    logger.info("Web UI login: %s (role=%s)", email, user["role"])
    return resp


@router.get("/auth/logout")
async def logout(request: Request):
    resp = RedirectResponse("/auth/login", status_code=302)
    clear_session(resp)
    return resp


def _deny(request: Request, message: str) -> HTMLResponse:
    return _templates().TemplateResponse(
        request, "auth/login.html",
        {"domain": settings.ALLOWED_EMAIL_DOMAIN, "configured": _oauth_ready(), "error": message},
        status_code=403,
    )
