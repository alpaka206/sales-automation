"""Email template CRUD web routes + revision history.

CRUD with a revision snapshot taken before each edit/delete, plus a history
view. This module owns only the editable store + UI; integration with the send
path is done elsewhere.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..auth import actor_name

from ....db.models import EmailTemplate, EmailTemplateRevision
from ....db.session import SessionLocal
from ._shared import templates

router = APIRouter(tags=["web"])


def _snapshot_revision(session, tpl: EmailTemplate, change_note: str, edited_by: str) -> None:
    """Append the template's CURRENT state to the revision history."""
    session.add(
        EmailTemplateRevision(
            template_id=tpl.id,
            key=tpl.key,
            name=tpl.name,
            language=tpl.language or "all",
            channel=tpl.channel or "email",
            body=tpl.body,
            description=tpl.description,
            status=tpl.status or "active",
            change_note=change_note,
            edited_by=edited_by,
        )
    )


@router.get("/email-templates")
async def email_templates_list(request: Request):
    """List all email templates."""
    with SessionLocal() as session:
        rows = session.query(EmailTemplate).order_by(EmailTemplate.updated_at.desc()).all()
        items = [
            {
                "id": t.id,
                "key": t.key,
                "name": t.name,
                "language": t.language or "all",
                "status": t.status or "active",
                "version": t.version or 1,
                "updated_at": t.updated_at,
            }
            for t in rows
        ]
    return templates.TemplateResponse(request, "email_templates_list.html", {"templates": items})


@router.get("/email-templates/new")
async def email_templates_new(request: Request):
    """Form to create a new email template."""
    return templates.TemplateResponse(
        request, "email_templates_form.html", {"tpl": None, "mode": "create"}
    )


@router.get("/email-templates/{tpl_id}")
async def email_templates_edit(request: Request, tpl_id: int):
    """Edit form for an existing email template."""
    with SessionLocal() as session:
        tpl = session.get(EmailTemplate, tpl_id)
        if not tpl:
            raise HTTPException(status_code=404, detail="템플릿을 찾을 수 없습니다")
        item = {
            "id": tpl.id,
            "key": tpl.key,
            "name": tpl.name,
            "language": tpl.language or "all",
            "channel": tpl.channel or "email",
            "description": tpl.description or "",
            "status": tpl.status or "active",
            "version": tpl.version or 1,
            "body": tpl.body,
        }
    return templates.TemplateResponse(
        request, "email_templates_form.html", {"tpl": item, "mode": "edit"}
    )


@router.post("/email-templates")
async def email_templates_create(
    request: Request,
    key: str = Form(""),
    name: str = Form(""),
    language: str = Form("all"),
    description: str = Form(""),
    status: str = Form("active"),
    body: str = Form(""),
):
    """Create a new email template and record its first revision."""
    author = actor_name(request, fallback="") or "web"
    if not key.strip() or not name.strip():
        return HTMLResponse(
            '<div class="text-red-600 text-sm">키와 이름은 필수입니다</div>',
            status_code=400,
        )
    with SessionLocal() as session:
        existing = session.query(EmailTemplate).filter_by(key=key.strip()).first()
        if existing:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">이미 존재하는 키입니다</div>',
                status_code=400,
            )
        tpl = EmailTemplate(
            key=key.strip(),
            name=name.strip(),
            language=language.strip() or "all",
            channel="email",
            description=description.strip() or None,
            status=status.strip() or "active",
            version=1,
            author=author,
            body=body,
        )
        session.add(tpl)
        session.flush()
        _snapshot_revision(session, tpl, change_note="created", edited_by=author)
        session.commit()
    return HTMLResponse(
        '<div class="text-green-600 text-sm font-medium">템플릿 생성 완료</div>'
        '<script>setTimeout(()=>location.href="/email-templates",500)</script>'
    )


@router.put("/email-templates/{tpl_id}")
async def email_templates_update(
    tpl_id: int,
    request: Request,
    name: str = Form(""),
    language: str = Form("all"),
    description: str = Form(""),
    status: str = Form("active"),
    body: str = Form(""),
    change_note: str = Form(""),
):
    """Update an email template, snapshotting the prior state into history."""
    author = actor_name(request, fallback="") or "web"
    with SessionLocal() as session:
        tpl = session.get(EmailTemplate, tpl_id)
        if not tpl:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">템플릿을 찾을 수 없습니다</div>',
                status_code=404,
            )
        # Snapshot the current (pre-edit) state, then bump version and apply.
        _snapshot_revision(
            session,
            tpl,
            change_note=change_note.strip() or "edited",
            edited_by=author,
        )
        if name.strip():
            tpl.name = name.strip()
        tpl.language = language.strip() or "all"
        tpl.description = description.strip() or None
        tpl.status = status.strip() or "active"
        tpl.version = (tpl.version or 1) + 1
        tpl.body = body
        session.commit()
        new_version = tpl.version
    return HTMLResponse(
        f'<div class="text-green-600 text-sm font-medium">저장 완료 (v{new_version})</div>'
    )


@router.delete("/email-templates/{tpl_id}")
async def email_templates_delete(tpl_id: int, request: Request):
    """Delete an email template (keeps its revision history)."""
    editor = actor_name(request, fallback="web") or "web"
    with SessionLocal() as session:
        tpl = session.get(EmailTemplate, tpl_id)
        if not tpl:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">템플릿을 찾을 수 없습니다</div>',
                status_code=404,
            )
        _snapshot_revision(session, tpl, change_note="deleted", edited_by=editor)
        session.delete(tpl)
        session.commit()
    return HTMLResponse(
        '<div class="text-orange-600 text-sm font-medium">삭제 완료</div>'
        '<script>setTimeout(()=>location.href="/email-templates",500)</script>'
    )


@router.get("/email-templates/{tpl_id}/history")
async def email_templates_history(request: Request, tpl_id: int):
    """Show the revision history for an email template."""
    with SessionLocal() as session:
        tpl = session.get(EmailTemplate, tpl_id)
        revs = (
            session.query(EmailTemplateRevision)
            .filter_by(template_id=tpl_id)
            .order_by(EmailTemplateRevision.created_at.desc())
            .all()
        )
        current = (
            {"id": tpl.id, "name": tpl.name, "version": tpl.version or 1}
            if tpl
            else {"id": tpl_id, "name": "(삭제됨)", "version": "-"}
        )
        items = [
            {
                "key": r.key,
                "name": r.name,
                "language": r.language or "all",
                "change_note": r.change_note or "",
                "edited_by": r.edited_by or "",
                "status": r.status,
                "description": r.description or "",
                "body": r.body,
                "created_at": r.created_at,
            }
            for r in revs
        ]
    return templates.TemplateResponse(
        request, "email_templates_history.html", {"tpl": current, "revisions": items}
    )
