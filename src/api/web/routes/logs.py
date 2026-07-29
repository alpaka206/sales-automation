"""Operations screen — durable failures to act on, and the recent problem log.

Two tabs, one page. They answer the same operator question ("what is broken?") from
opposite ends: the recovery tab is DB-backed work with retry/resolve actions, the log
tab is the in-memory WARNING+/HTTP-error buffer that explains why. The recovery
console used to live at /operations/recovery; that URL now redirects here.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ....common import log_buffer
from ..auth import admin_required
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


_TABS = ("recovery", "log")


@router.get("/logs")
async def logs_page(request: Request, view: str = "all", tab: str = "recovery"):
    """Operations screen (admins only — the log tab may contain message content).

    Defaults to the recovery tab: it is the one with work on it. The log tab is for
    diagnosing what the recovery tab shows.
    """
    if not admin_required(request):
        return _forbidden()
    from .recovery import recovery_context, recovery_pending_count

    view = view if view in _VIEWS else "all"
    tab = tab if tab in _TABS else "recovery"
    # Always loaded: the tab strip shows the outstanding count even while the log tab
    # is open, so a failure that appears while you are reading logs is not invisible.
    recovery = recovery_context()
    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            **recovery,
            "recovery_pending": recovery_pending_count(recovery),
            "events": _events(view),
            "counts": log_buffer.counts(),
            "view": view,
            "tab": tab,
        },
    )


@router.get("/logs/rows")
async def logs_rows(request: Request, view: str = "all"):
    """Just the table rows — polled by the page to stay live."""
    if not admin_required(request):
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
    if not admin_required(request):
        return _forbidden()
    log_buffer.clear()
    return Response(status_code=204, headers={"HX-Redirect": "/logs"})
