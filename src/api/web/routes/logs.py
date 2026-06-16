"""Problem-log viewer — recent WARNING+ logs and HTTP 4xx/5xx, for developers."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ....common import log_buffer
from ..auth import is_admin
from ._shared import templates

router = APIRouter(tags=["web"])

# view -> (level filter, kind filter)
_VIEWS = {
    "all": (None, None),
    "error": ("ERROR", None),
    "warning": ("WARNING", None),
    "http": (None, "http"),
}


def _events(view: str) -> list[dict]:
    level, kind = _VIEWS.get(view, (None, None))
    return [
        {
            "ts": e.ts,
            "level": e.level,
            "source": e.source,
            "message": e.message,
            "kind": e.kind,
        }
        for e in log_buffer.recent(level=level, kind=kind, limit=300)
    ]


def _forbidden() -> Response:
    return Response(content="관리자만 접근할 수 있습니다.", status_code=403)


@router.get("/logs")
async def logs_page(request: Request, view: str = "all"):
    """Full log viewer page (admins only — logs may contain message content)."""
    if not is_admin(request):
        return _forbidden()
    view = view if view in _VIEWS else "all"
    return templates.TemplateResponse(
        request,
        "logs.html",
        {"events": _events(view), "counts": log_buffer.counts(), "view": view},
    )


@router.get("/logs/rows")
async def logs_rows(request: Request, view: str = "all"):
    """Just the table rows — polled by the page to stay live."""
    if not is_admin(request):
        return _forbidden()
    view = view if view in _VIEWS else "all"
    return templates.TemplateResponse(
        request,
        "partials/log_rows.html",
        {"events": _events(view), "view": view},
    )


@router.post("/logs/clear")
async def logs_clear(request: Request):
    """Empty the in-memory log buffer (admins only)."""
    if not is_admin(request):
        return _forbidden()
    log_buffer.clear()
    return Response(status_code=204, headers={"HX-Redirect": "/logs"})
