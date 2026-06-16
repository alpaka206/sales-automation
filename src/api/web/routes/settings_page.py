"""Settings page: health checks, env var overview, and LLM usage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from fastapi import Form
from fastapi.responses import Response

from ....common.config import settings
from ....db.models import LLMUsage, User
from ....db.session import SessionLocal
from ..auth import is_admin, session_user
from ._shared import esc, templates

router = APIRouter(tags=["web"])


def _settings_context() -> dict:
    """Fast settings-page data: LLM usage only.

    Health checks call external services and are slow, so they are NOT run here —
    the page loads instantly and pulls them in lazily via /settings/healthcheck.
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    try:
        with SessionLocal() as session:
            today_llm = (
                session.scalar(
                    select(func.count())
                    .select_from(LLMUsage)
                    .where(LLMUsage.created_at >= today_start)
                )
                or 0
            )
            week_llm = (
                session.scalar(
                    select(func.count())
                    .select_from(LLMUsage)
                    .where(LLMUsage.created_at >= week_start)
                )
                or 0
            )
    except Exception:
        today_llm = 0
        week_llm = 0

    return {
        "today_llm": today_llm,
        "week_llm": week_llm,
        "llm_provider": settings.LLM_PROVIDER,
    }


def _healthcheck_context() -> dict:
    """Run health checks (slow — external services). Used by the lazy fragment."""
    from ....common.healthcheck import run_healthchecks

    report = run_healthchecks()
    _llm_checks = [c for c in report.checks if c.name == "Gemini (Vertex)"]
    llm_ok = all(c.status != "FAIL" for c in _llm_checks) if _llm_checks else True
    return {
        "checks": [c.model_dump() for c in report.checks],
        "overall_status": report.overall_status,
        "llm_ok": llm_ok,
    }


@router.get("/settings")
async def settings_page(request: Request):
    """System settings and LLM usage. Health checks load lazily (see fragment)."""
    return templates.TemplateResponse(request, "settings.html", _settings_context())


@router.get("/settings/healthcheck")
async def settings_healthcheck(request: Request):
    """Lazy-loaded health-check fragment (hx-trigger=load + manual refresh)."""
    return templates.TemplateResponse(request, "partials/healthcheck.html", _healthcheck_context())


# --------------------------------------------------------------------------- #
# User access management (allowlist) — admin only, Google-OAuth mode
# --------------------------------------------------------------------------- #
def _forbidden() -> Response:
    return Response(content="관리자만 접근할 수 있습니다.", status_code=403)


@router.get("/settings/users")
async def settings_users(request: Request):
    """Allowlist management: approve/revoke who may use the console (admins only)."""
    if not is_admin(request):
        return _forbidden()
    with SessionLocal() as session:
        rows = (
            session.query(User)
            .order_by(User.approved.desc(), User.role.desc(), User.created_at.asc())
            .all()
        )

        def _row(u: User) -> dict:
            return {
                "email": u.email,
                "name": u.name or "",
                "role": u.role,
                "approved": u.approved,
                "last_login_at": u.last_login_at,
            }

        # Split into "registered" (approved) vs "applied / pending approval".
        # A pending row is someone who signed in (or was added) but isn't approved.
        approved_users = [_row(u) for u in rows if u.approved]
        pending_users = [_row(u) for u in rows if not u.approved]
    me = session_user(request) or {}
    return templates.TemplateResponse(
        request,
        "settings_users.html",
        {
            "approved_users": approved_users,
            "pending_users": pending_users,
            "me_email": me.get("email", ""),
            "domain": settings.ALLOWED_EMAIL_DOMAIN,
        },
    )


@router.post("/settings/users/add")
async def settings_user_add(
    request: Request,
    username: str = Form(""),
    email: str = Form(""),
    role: str = Form("member"),
):
    """Pre-add an email to the allowlist (admins only).

    Lets an admin grant access before the user's first login, instead of waiting
    for them to sign in and land in the pending queue. The row is created already
    approved; the user just signs in with Google and is let straight through.

    The form sends only the local part (``username``); the @domain is fixed and
    appended here. ``email`` is still accepted as a fallback for the full address.
    """
    if not is_admin(request):
        return _forbidden()

    domain = (settings.ALLOWED_EMAIL_DOMAIN or "").lower().strip()
    local = (username or "").strip().lower().lstrip("@")
    if "@" in local:  # tolerate a pasted full address in the username box
        local = local.split("@", 1)[0]
    if local and domain:
        email = f"{local}@{domain}"
    else:
        email = (email or "").strip().lower()

    def _err(msg: str) -> HTMLResponse:
        # 200 (not 4xx) so htmx swaps the banner into #add-user-msg — htmx 2.x does
        # not swap error-status responses by default.
        return HTMLResponse(
            f'<div class="banner banner--danger" style="padding:10px 12px">{esc(msg)}</div>'
        )

    if not email or "@" not in email:
        return _err("올바른 이메일 주소를 입력하세요.")
    if domain and not email.endswith("@" + domain):
        return _err(f"@{domain} 도메인 계정만 추가할 수 있습니다.")

    role = "admin" if role == "admin" else "member"
    with SessionLocal() as session:
        u = session.get(User, email)
        if u:
            # Already present — just (re)approve and apply the chosen role.
            u.approved = True
            u.role = role
        else:
            session.add(User(email=email, approved=True, role=role))
        session.commit()

    return Response(status_code=204, headers={"HX-Redirect": "/settings/users"})


@router.post("/settings/users/{email}")
async def settings_user_update(request: Request, email: str, action: str = Form("")):
    """Approve / revoke / change role for a user. Admins can't lock themselves out."""
    if not is_admin(request):
        return _forbidden()
    me = session_user(request) or {}
    email = email.lower()
    if email == (me.get("email") or "").lower() and action in (
        "revoke",
        "reject",
        "delete",
        "make_member",
    ):
        return HTMLResponse(
            '<div class="banner banner--danger" style="padding:10px 12px">자기 자신의 권한은 해제할 수 없습니다</div>',
            status_code=400,
        )
    with SessionLocal() as session:
        u = session.get(User, email)
        if u:
            if action == "approve":
                u.approved = True
            elif action in ("revoke", "reject", "delete"):
                # Revoking a registered user (or rejecting a pending applicant)
                # removes the row entirely — they don't fall back to the pending
                # queue. Signing in again re-creates a fresh pending application.
                session.delete(u)
            elif action == "make_admin":
                u.role = "admin"
                u.approved = True
            elif action == "make_member":
                u.role = "member"
            session.commit()
    # htmx: reload the page to reflect the change
    return Response(status_code=204, headers={"HX-Redirect": "/settings/users"})
