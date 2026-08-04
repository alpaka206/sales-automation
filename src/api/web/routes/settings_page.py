"""User access management (allowlist) — admin only, Google-OAuth mode.

The general Settings page was removed from the web UI; only the Google-OAuth
user-approval routes remain (hidden in basic-auth mode).
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from ....common.config import settings
from ....db.models import User
from ....db.session import SessionLocal
from ..auth import is_admin, normalize_role, session_user
from ._shared import esc

router = APIRouter(tags=["web"])


# --------------------------------------------------------------------------- #
# User access management (allowlist) — admin only, Google-OAuth mode
# --------------------------------------------------------------------------- #
def _forbidden() -> Response:
    return Response(content="관리자만 접근할 수 있습니다.", status_code=403)


@router.post("/settings/users/add")
async def settings_user_add(
    request: Request,
    username: str = Form(""),
    email: str = Form(""),
    role: str = Form("admin"),
):
    """Add an email to the allowlist (admins only). This is the ONLY way in.

    There is no pending queue: an address that was never added here cannot reach
    the console. The row is created already approved, so the user just signs in
    with Google and is let straight through.

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

    role = normalize_role(role)
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
        "make_viewer",
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
                # Removes the row entirely. Signing in again does NOT restore
                # access — an admin has to add the address back.
                session.delete(u)
            # Two roles only: "admin" (운영자, full access) and "viewer" (read-only).
            elif action in ("make_admin", "make_member", "make_operator"):
                u.role = "admin"
                u.approved = True
            elif action == "make_viewer":
                u.role = "viewer"
            session.commit()
    # htmx: reload the page to reflect the change
    return Response(status_code=204, headers={"HX-Redirect": "/settings/users"})
