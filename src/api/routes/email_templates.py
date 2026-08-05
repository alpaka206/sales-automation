"""Email template CRUD web routes + revision history.

CRUD with a revision snapshot taken before each edit/delete, plus a history
view. This module owns only the editable store + UI; integration with the send
path is done elsewhere.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ..auth import actor_name

from ...db.email_templates import SIGNATURE_KEY_PREFIX
from ...db.models import EmailTemplate, EmailTemplateRevision
from ...db.session import SessionLocal

router = APIRouter(tags=["web"])


def _generate_key(session, name: str, language: str) -> str:
    """Derive a unique storage key from the name, so the operator never types one.

    The key is a code reference: ``auto_ack``, ``signature_ko`` and the reply-format row
    are fetched by exact key from the send path, and the compose screen's signature picker
    lists everything under ``signature_html_``. Those rows already exist and are only ever
    edited — so a template CREATED here can be reached by exactly one thing, that picker,
    and it gets the prefix that puts it there. A key with any other shape would produce a
    row nothing in the app can ever read.
    """
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    # A Korean name romanizes to nothing; fall back to the language it is written for.
    base = base or (language if language and language != "all" else "custom")
    candidate = f"{SIGNATURE_KEY_PREFIX}{base}"[:100]
    suffix = 2
    while session.query(EmailTemplate).filter_by(key=candidate).first():
        candidate = f"{SIGNATURE_KEY_PREFIX}{base}_{suffix}"[:100]
        suffix += 1
    return candidate


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


@router.post("/email-templates")
async def email_templates_create(
    request: Request,
    name: str = Form(""),
    language: str = Form("all"),
    body: str = Form(""),
):
    """Create a new email template and record its first revision.

    The key is derived from the name (see ``_generate_key``) rather than asked for, and
    the status is always ``active`` — see the note on the update handler.
    """
    author = actor_name(request, fallback="") or "web"
    if not name.strip():
        return HTMLResponse(
            '<div class="text-red-600 text-sm">템플릿 이름은 필수입니다</div>',
            status_code=400,
        )
    with SessionLocal() as session:
        tpl = EmailTemplate(
            key=_generate_key(session, name.strip(), language.strip()),
            name=name.strip(),
            language=language.strip() or "all",
            channel="email",
            status="active",
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
    body: str = Form(""),
):
    """Update an email template, snapshotting the prior state into history.

    ``key``, ``description`` and ``status`` are deliberately absent. The key is a code
    reference the send path resolves and must not move; the description was a field
    nothing ever displayed; and only ``active`` rows are ever read, so draft/archived
    described a template that exists and does nothing. Saving revives a dormant row
    rather than leaving it unreachable now that nothing can set the value back.
    """
    author = actor_name(request, fallback="") or "web"
    with SessionLocal() as session:
        tpl = session.get(EmailTemplate, tpl_id)
        if not tpl:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">템플릿을 찾을 수 없습니다</div>',
                status_code=404,
            )
        # Snapshot the current (pre-edit) state, then bump version and apply.
        _snapshot_revision(session, tpl, change_note="edited", edited_by=author)
        if name.strip():
            tpl.name = name.strip()
        tpl.language = language.strip() or "all"
        tpl.status = "active"
        tpl.version = (tpl.version or 1) + 1
        tpl.body = body
        session.commit()
    # The version still increments — it orders the revision history — it is just not a
    # number anyone reads off a screen.
    return HTMLResponse('<div class="text-green-600 text-sm font-medium">저장 완료</div>')


@router.post("/email-templates/{tpl_id}/default")
async def email_templates_set_default(tpl_id: int):
    """Choose the signature every new draft starts with.

    0046 made this a row instead of a literal in ``inbound.py``, but nothing ever called
    ``set_default_signature`` — so the flag stayed on whoever the migration carried over
    and could not be moved without SQL. The console is where that decision belongs.

    Signatures only: the other rows are code references the send path resolves by name,
    and stamping a draft with the reply-format row would put the model's instructions in
    front of a customer.
    """
    from ...db.email_templates import set_default_signature

    with SessionLocal() as session:
        tpl = session.get(EmailTemplate, tpl_id)
        if not tpl:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">템플릿을 찾을 수 없습니다</div>',
                status_code=404,
            )
        if not (tpl.key or "").startswith(SIGNATURE_KEY_PREFIX):
            return HTMLResponse(
                '<div class="text-red-600 text-sm">서명만 기본으로 지정할 수 있습니다</div>',
                status_code=400,
            )
        key = tpl.key
    set_default_signature(key)
    return HTMLResponse('<div class="text-green-600 text-sm font-medium">기본 서명 변경</div>')


@router.delete("/email-templates/{tpl_id}")
async def email_templates_delete(tpl_id: int, request: Request):
    """Delete a SIGNATURE (keeps its revision history). Nothing else deletes.

    Every other row is a key the code resolves by name — ``auto_ack``, ``auto_ack_en``,
    ``reply_format``, the two links, the two sender names. Deleting one does not remove a
    feature, it removes the answer to a lookup that still happens: an acknowledgement that
    silently falls back to a hardcoded string, a reply ending on a literal
    ``{{MEETING_LINK}}``, an English mail introducing the writer as "배운태".

    And nothing could put it back — the console creates signatures, not code references.
    Not using one means clearing its body, which is visible and reversible.
    """
    editor = actor_name(request, fallback="web") or "web"
    with SessionLocal() as session:
        tpl = session.get(EmailTemplate, tpl_id)
        if not tpl:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">템플릿을 찾을 수 없습니다</div>',
                status_code=404,
            )
        if not (tpl.key or "").startswith(SIGNATURE_KEY_PREFIX):
            return HTMLResponse(
                '<div class="text-red-600 text-sm">'
                "발송 경로가 이름으로 찾는 템플릿입니다. 지울 수 없고, 쓰지 않으려면 "
                "내용을 비우세요.</div>",
                status_code=400,
            )
        _snapshot_revision(session, tpl, change_note="deleted", edited_by=editor)
        session.delete(tpl)
        session.commit()
    return HTMLResponse(
        '<div class="text-orange-600 text-sm font-medium">삭제 완료</div>'
        '<script>setTimeout(()=>location.href="/email-templates",500)</script>'
    )


