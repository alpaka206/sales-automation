"""Public unsubscribe route (recipient-facing; carries its own signed token)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["web"])


@router.get("/unsubscribe")
async def unsubscribe(request: Request, email: str = "", token: str = ""):
    """Handle unsubscribe link clicks."""
    from ....integrations.compliance import suppress_email, verify_unsub_token

    if not email or not token:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;padding:40px;text-align:center'>"
            "<h2>잘못된 요청입니다.</h2></body></html>",
            status_code=400,
        )
    if not verify_unsub_token(email, token):
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;padding:40px;text-align:center'>"
            "<h2>유효하지 않은 링크입니다.</h2></body></html>",
            status_code=400,
        )
    suppress_email(email, reason="unsubscribe")
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;padding:40px;text-align:center'>"
        "<h2>수신 거부 처리가 완료되었습니다.</h2>"
        f"<p>{email} 주소로 더 이상 메일을 보내지 않습니다.</p>"
        "</body></html>"
    )
