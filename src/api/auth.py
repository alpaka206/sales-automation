"""Google OAuth sign-in for the web UI, restricted to a company domain + allowlist.

No heavy deps: the OAuth code exchange uses httpx, the ID token is verified with
``google.oauth2.id_token`` (already installed via google-genai), and the session is a
stdlib-HMAC-signed cookie (no Authlib / itsdangerous / server-side session store).

Gate (enforced in :func:`oauth_callback`):
  1. Google ID token signature/aud/exp verified by Google's library.
  2. ``email_verified`` is true AND the email is on ``ALLOWED_EMAIL_DOMAIN``.
  3. The email is approved — an existing ``users`` row with ``approved=True``. Everyone
     else lands on a "pending approval" page until an admin approves them in the UI.

Operators live in the ``users`` table only; there is no env-var allowlist. The very
first sign-in on an empty table bootstraps that account as an approved admin (see
:func:`_login_or_pending`), which is safe because step 2 already restricts sign-in to
the company domain. ``scripts/bootstrap_admin.py`` is the recovery path if the table
ever ends up with no admin.

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
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from ..common.config import settings
from ..db.models import User
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)


router = APIRouter(tags=["auth"])

SESSION_COOKIE = "perso_session"
STATE_COOKIE = "perso_oauth_state"
SESSION_TTL = 7 * 24 * 3600  # 7 days
STATE_TTL = 600  # 10 minutes

_GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


def normalize_role(role: str | None) -> str:
    """Collapse every stored role to one of the two the console actually has.

    There are exactly two: ``admin`` (labelled 운영자 in the UI — full access,
    including user management) and ``viewer`` (read-only). The former separate
    "operator" tier was merged into admin, so legacy ``operator``/``member`` rows
    resolve to full access rather than being stranded on a tier that no longer
    exists. Only an explicit ``viewer`` is restricted.
    """
    return "viewer" if role == "viewer" else "admin"


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
    """Whether THIS session belongs to an admin, by the role in the users table.

    Answers a narrower question than :func:`admin_required` and is not a gate: with no
    session it is False, which in basic mode is every request. Use it to decide what to
    show someone, never whether to let them in.
    """
    u = session_user(request)
    return bool(u and u.get("role") == "admin")


def admin_required(request: Request) -> bool:
    """The ONE gate for admin-only screens. The users table decides.

    There used to be two, and they disagreed. `is_admin` alone locks out everybody in
    basic mode — there is no session user to have a role — so 접근 승인 was permanently
    "관리자만 접근할 수 있습니다" to an operator who *is* an admin in the database, while
    운영 로그 right beside it opened fine. Whether an admin got in depended on which of
    the two functions the route happened to import.

    The rule, in one place:

      * a session -> ``normalize_role`` of that user's stored role, read from the DB on
        every request (a role changed in 접근 승인 takes effect immediately, no re-login)
      * no session -> basic mode, which has no operator directory at all. Getting this
        far already means localhost or the shared WEB_UI_PASSWORD, and that same door
        allows sending mail and moving stages. Refusing only the admin screens there
        would not be security, just an unreachable screen.
    """
    if session_user(request) is not None:
        return is_admin(request)
    return settings.AUTH_MODE != "google_oauth"


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
# Domain gate + DB-backed operator directory
# --------------------------------------------------------------------------- #
def _domain_ok(email: str) -> bool:
    dom = (settings.ALLOWED_EMAIL_DOMAIN or "").lower().strip()
    return bool(dom) and email.lower().endswith("@" + dom)


def _login_or_pending(email: str, name: str | None, picture: str | None) -> tuple[dict, bool]:
    """Upsert the user on login and return ({email,name,role}, approved).

    Authorization lives entirely in the ``users`` table. The single exception is
    bootstrap: on a brand-new deployment the table is empty and nobody could ever
    approve anybody, so the first account to sign in is created as an approved
    admin. The caller has already verified the Google ID token and enforced
    ``ALLOWED_EMAIL_DOMAIN``, so this can only ever be a company account. Once any
    row exists the branch is dead and every newcomer lands as pending.
    """
    email = email.lower()
    with SessionLocal() as session:
        user = session.get(User, email)
        if not user:
            bootstrap = session.query(User).count() == 0
            if bootstrap:
                logger.warning("Bootstrapping first admin from empty users table: %s", email)
            # Only two roles exist; "admin" is the full-access one. The access gate
            # is `approved`, not the role, so a non-bootstrap newcomer is created
            # unapproved and stays locked out until an admin adds them.
            user = User(email=email, approved=bootstrap, role="admin")
            session.add(user)
        if name:
            user.name = name
        if picture:
            user.picture = picture
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


def _sign_in_document(status_code: int = 200) -> Response:
    """The console's own document, rendered before there is a session.

    /auth/* is the one prefix the auth middleware lets through unauthenticated, and the
    SPA bundle lives under /static which it also lets through — so React can draw the
    sign-in screen without anything about the gate changing. Keeping the URL is the
    point: a redirect into /app would have to be excepted from the very check that
    sends people here.
    """
    from .main import spa_document

    return spa_document(status_code=status_code)


@router.get("/auth/state")
async def auth_state(request: Request):
    """What the sign-in screen needs to draw itself. Readable without a session."""
    user = current_user(request)
    return {
        "domain": settings.ALLOWED_EMAIL_DOMAIN,
        "configured": _oauth_ready(),
        "email": (user or {}).get("email", ""),
    }


@router.get("/auth/login")
async def login_page(request: Request):
    """Landing page with a 'Sign in with Google' button."""
    if current_user(request):
        return RedirectResponse("/", status_code=302)
    return _sign_in_document()


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
        return _deny(f"Google 로그인 취소/오류: {error}")
    cookie_state = request.cookies.get(STATE_COOKIE, "")
    if not code or not state or state != cookie_state or _unsign(state) is None:
        return _deny("잘못된 로그인 요청입니다 (state 불일치). 다시 시도하세요.")

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
        return _deny("로그인 검증에 실패했습니다. 다시 시도하세요.")

    email = (claims.get("email") or "").lower()
    if not claims.get("email_verified") or not _domain_ok(email):
        # The address they tried is deliberately NOT echoed: the message now travels as a
        # query parameter, and query strings end up in proxy and access logs.
        logger.info("Web UI login refused (domain/verification): %s", email or "?")
        return _deny(f"{settings.ALLOWED_EMAIL_DOMAIN} 계정으로만 로그인할 수 있습니다.")

    user, approved = _login_or_pending(email, claims.get("name"), claims.get("picture"))
    if not approved:
        return _sign_in_document(status_code=403)

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


def _deny(message: str) -> Response:
    """Refuse a sign-in and say why, on the screen they came from.

    The reason rides in the query string because the document is static — it is the same
    bundle for everyone, so nothing about the attempt can be baked into it.
    """
    return RedirectResponse(
        f"/auth/login?error={quote(message)}", status_code=303
    )
