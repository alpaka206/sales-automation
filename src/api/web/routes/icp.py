"""ICP scoring-rule web routes (per outbound source)."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ....db.models import ICPRule
from ....db.session import SessionLocal
from ._shared import templates

router = APIRouter(tags=["web"])

_DEFAULT_SOURCES = ("youtube", "linkedin_comments", "google_search", "job_board", "manual_csv")


@router.get("/icp-rules")
async def icp_rules_list(request: Request):
    """List ICP scoring rules by source."""
    with SessionLocal() as session:
        rules = session.query(ICPRule).order_by(ICPRule.source).all()
        items = {r.source: {"id": r.id, "source": r.source, "enabled": r.enabled,
                            "criteria_md": r.criteria_md, "updated_at": r.updated_at} for r in rules}
    all_sources = []
    for s in _DEFAULT_SOURCES:
        if s in items:
            all_sources.append(items[s])
        else:
            all_sources.append({"id": None, "source": s, "enabled": False, "criteria_md": "", "updated_at": None})
    for s, r in items.items():
        if s not in _DEFAULT_SOURCES:
            all_sources.append(r)
    return templates.TemplateResponse(request, "icp_rules_list.html", {"rules": all_sources})


@router.get("/icp-rules/{source}/edit")
async def icp_rules_edit(request: Request, source: str):
    """Edit form for a source's ICP criteria."""
    with SessionLocal() as session:
        rule = session.query(ICPRule).filter_by(source=source).first()
        item = {
            "source": source,
            "criteria_md": rule.criteria_md if rule else "",
            "enabled": rule.enabled if rule else True,
        }
    return templates.TemplateResponse(request, "icp_rules_form.html", {"rule": item})


@router.post("/icp-rules/{source}")
async def icp_rules_save(
    source: str,
    criteria_md: str = Form(""),
    enabled: str = Form("on"),
):
    """Create or update ICP criteria for a source."""
    is_enabled = enabled in ("on", "true", "1")
    with SessionLocal() as session:
        rule = session.query(ICPRule).filter_by(source=source).first()
        if rule:
            rule.criteria_md = criteria_md.strip()
            rule.enabled = is_enabled
        else:
            rule = ICPRule(source=source, criteria_md=criteria_md.strip(), enabled=is_enabled)
            session.add(rule)
        session.commit()
    return HTMLResponse(
        '<div class="text-green-600 text-sm font-medium">저장 완료</div>'
    )
