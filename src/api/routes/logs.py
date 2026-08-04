"""Operations screen — durable failures to act on, and the recent problem log.

Two tabs, one page. They answer the same operator question ("what is broken?") from
opposite ends: the recovery tab is DB-backed work with retry/resolve actions, the log
tab is the in-memory WARNING+/HTTP-error buffer that explains why. The recovery
console used to live at /operations/recovery; that URL now redirects here.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ...common import log_buffer
from ..auth import admin_required

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


@router.post("/logs/clear")
async def logs_clear(request: Request):
    """Empty the in-memory log buffer (admins only)."""
    if not admin_required(request):
        return _forbidden()
    log_buffer.clear()
    return Response(status_code=204, headers={"HX-Redirect": "/logs"})
