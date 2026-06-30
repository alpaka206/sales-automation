"""Operator tools — the business-plan (quote) calculator embedded in compose.

The calculator is an INTERNAL sales reference (it carries margin / credit policy
data), so it is served only behind the web-UI auth gate via these routes, never
from the public ``/static`` mount. The compose screen embeds ``/app`` in an
iframe; ``/tools/quote-calculator`` is a full-page view linked from the nav.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from ._shared import templates

router = APIRouter(tags=["web"])

_CALC_FILE = Path(__file__).resolve().parents[1] / "assets" / "quote_calculator.html"


@router.get("/tools/quote-calculator")
async def quote_calculator_page(request: Request):
    """Full-page calculator view (nav target + 'open large' link)."""
    return templates.TemplateResponse(request, "quote_calculator_page.html", {})


@router.get("/tools/quote-calculator/app")
async def quote_calculator_app() -> Response:
    """Raw calculator HTML for the iframe. Auth-gated like all web-UI paths."""
    if _CALC_FILE.exists():
        return FileResponse(str(_CALC_FILE), media_type="text/html; charset=utf-8")
    return HTMLResponse(
        "<p style='font-family:sans-serif;padding:16px'>계산기 파일을 찾을 수 없습니다.</p>",
        status_code=404,
    )
