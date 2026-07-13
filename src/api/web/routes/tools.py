"""Operator tools — the business-plan (quote) calculator embedded in compose.

The calculator is an INTERNAL sales reference (it carries margin / credit policy
data), so it is served only behind the web-UI auth gate via these routes, never
from the public ``/static`` mount. The compose screen embeds ``/app`` in an
iframe; ``/tools/quote-calculator`` is a full-page view linked from the nav.

The tier pricing table is NOT hardcoded in the client any more — it lives in
``src/common/quote_tiers.py`` (the single source of truth, unit-tested) and is
injected into the calculator template here.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ....common.quote_tiers import policy_client_json
from ._shared import templates

router = APIRouter(tags=["web"])


@router.get("/tools/quote-calculator")
async def quote_calculator_page(request: Request):
    """Full-page calculator view (nav target + 'open large' link)."""
    return templates.TemplateResponse(request, "quote_calculator_page.html", {})


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
