"""Settings page: health checks, env var overview, and LLM usage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from ....common.config import settings
from ....db.models import LLMUsage
from ....db.session import SessionLocal
from ._shared import esc, templates

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
    # Pill tone mirrors STATUS/TONE in templates/partials/ui.html (PASS→ok, WARN→warn, FAIL→danger).
    tone = {"PASS": "ok", "WARN": "warn", "FAIL": "danger"}
    ko = {"PASS": "정상", "WARN": "주의", "FAIL": "실패"}
    rows = ""
    for c in report.checks:
        t = tone.get(c.status, "neutral")
        label = ko.get(c.status, c.status)
        rows += (
            f'<tr><td style="font-weight:600">{esc(c.name)}</td>'
            f'<td><span class="pill pill--{t} pill--sm"><span class="pill__dot"></span>{esc(label)}</span></td>'
            f'<td class="td-subtle t-sm">{esc(c.detail)}</td>'
            f'<td class="td-subtle tnum">{c.latency_ms}ms</td></tr>'
        )
    return HTMLResponse(rows)
