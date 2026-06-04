"""Settings page: health checks, env var overview, and LLM usage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from ....common.config import settings
from ....db.models import LLMUsage
from ....db.session import SessionLocal
from ._shared import templates

router = APIRouter(tags=["web"])


def _mask_value(key: str, val: str) -> str:
    """Mask sensitive env var values."""
    if not val:
        return "(미설정)"
    sensitive = ("token", "key", "secret", "password", "cookie")
    if any(s in key.lower() for s in sensitive):
        return val[:4] + "***" if len(val) > 4 else "***"
    return val


def _settings_context() -> dict:
    """Build settings page data: healthcheck, env vars, LLM usage."""
    from ....common.healthcheck import run_healthchecks

    report = run_healthchecks()

    env_vars = []
    for field_name, field_info in settings.model_fields.items():
        val = str(getattr(settings, field_name, ""))
        env_vars.append({
            "name": field_name,
            "value": _mask_value(field_name, val),
        })

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    try:
        with SessionLocal() as session:
            today_llm = session.scalar(
                select(func.count()).select_from(LLMUsage).where(LLMUsage.created_at >= today_start)
            ) or 0
            week_llm = session.scalar(
                select(func.count()).select_from(LLMUsage).where(LLMUsage.created_at >= week_start)
            ) or 0
    except Exception:
        today_llm = 0
        week_llm = 0

    _llm_checks = [c for c in report.checks if c.name == "Gemini (Vertex)"]
    llm_ok = all(c.status != "FAIL" for c in _llm_checks) if _llm_checks else True

    return {
        "checks": [c.model_dump() for c in report.checks],
        "overall_status": report.overall_status,
        "env_vars": env_vars,
        "today_llm": today_llm,
        "week_llm": week_llm,
        "llm_provider": settings.LLM_PROVIDER,
        "llm_ok": llm_ok,
    }


@router.get("/settings")
async def settings_page(request: Request):
    """System settings, health checks, and env vars."""
    ctx = _settings_context()
    return templates.TemplateResponse(request, "settings.html", ctx)


@router.post("/settings/refresh-healthcheck")
async def settings_refresh_healthcheck():
    """Re-run health checks and return updated HTML."""
    from ....common.healthcheck import run_healthchecks

    report = run_healthchecks()
    rows = ""
    for c in report.checks:
        color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}.get(c.status, "gray")
        rows += (
            f'<tr class="border-b"><td class="px-4 py-2 text-sm">{c.name}</td>'
            f'<td class="px-4 py-2"><span class="text-xs font-medium text-{color}-700 '
            f'bg-{color}-100 px-2 py-0.5 rounded">{c.status}</span></td>'
            f'<td class="px-4 py-2 text-xs text-gray-500">{c.detail}</td>'
            f'<td class="px-4 py-2 text-xs text-gray-400">{c.latency_ms}ms</td></tr>'
        )
    return HTMLResponse(rows)
