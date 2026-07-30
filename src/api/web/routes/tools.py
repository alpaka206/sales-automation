"""Operator tools — the 활용 툴 sidebar section.

These are INTERNAL sales references (the calculator carries margin / credit policy
data), so they are served only behind the web-UI auth gate via these routes, never
from the public ``/static`` mount. All three open in a new tab from the sidebar.

The tier pricing table is NOT hardcoded in the client any more — it lives in
``src/common/quote_tiers.py`` (the single source of truth, unit-tested) and is
injected into the calculator template here.

견적서 / 계약서 are placeholders: the routes and the sidebar slots exist so the
navigation is settled, and the content lands later.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ....common.quote_tiers import policy_client_json
from ._shared import templates

router = APIRouter(tags=["web"])


# Placeholder tools: title + the sentence the operator should see until it is built.
_PLACEHOLDERS = {
    "quotation": ("견적서", "견적서 생성 기능은 준비 중입니다."),
    "contract": ("계약서", "계약서 생성 기능은 준비 중입니다."),
}


@router.get("/tools/quote-calculator")
async def quote_calculator_page(request: Request):
    """Full-page calculator view (nav target + 'open large' link)."""
    return templates.TemplateResponse(request, "quote_calculator_page.html", {})


@router.get("/tools/quotation")
async def quotation_page(request: Request):
    title, message = _PLACEHOLDERS["quotation"]
    return templates.TemplateResponse(
        request, "tool_placeholder.html", {"tool_title": title, "tool_message": message}
    )


@router.get("/tools/contract")
async def contract_page(request: Request):
    title, message = _PLACEHOLDERS["contract"]
    return templates.TemplateResponse(
        request, "tool_placeholder.html", {"tool_title": title, "tool_message": message}
    )


@router.get("/outbound-history")
async def outbound_history_page(request: Request):
    """수주 고객 — placeholder peer of 리드 히스토리 (/customers).

    Deliberately its OWN top-level path rather than /customers/outbound: Starlette
    matches on path shape, so a literal segment under /customers would be shadowed by
    ``/customers/{contact_id}`` in customer_ops, which is included first.
    """
    return templates.TemplateResponse(
        request,
        "tool_placeholder.html",
        {
            "tool_title": "수주 고객",
            "tool_message": "수주 고객 화면은 준비 중입니다.",
        },
    )


@router.get("/tools/quote-calculator/app")
async def quote_calculator_app(request: Request) -> Response:
    """Calculator HTML for the iframe, with the tier policy injected from Python.

    Auth-gated like all web-UI paths (the policy carries internal margin data via
    ``quote_tiers``, though ``cm`` is stripped before it reaches the client)."""
    return templates.TemplateResponse(
        request,
        "quote_calculator_app.html",
        {"policy_json": policy_client_json()},
    )
