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
from ...db.soft_delete import DELETED, utcnow

router = APIRouter(tags=["web"])


def _generate_key(session, name: str) -> str:
    """Derive a unique storage key from the name, so the operator never types one.

    The key is a code reference: ``auto_ack``, the reply-format row and the two links are
    fetched by exact key from the send path, and the review screen's signature picker
    lists everything under ``signature_``. Those rows already exist and are only ever
    edited — so a template CREATED here can be reached by exactly one thing, that picker,
    and it gets the prefix that puts it there. A key with any other shape would produce a
    row nothing in the app can ever read.
    """
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    # A Korean name romanizes to nothing. The key is never shown or typed, so the counter
    # below is the whole answer — there is no language to fall back to any more.
    base = base or "custom"
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
    body: str = Form(""),
):
    """Create a signature and record its first revision.

    The key is derived from the name (see ``_generate_key``) rather than asked for, the
    status is always ``active`` — see the note on the update handler — and the language is
    always ``all``: nothing matches a signature to a language, the operator picks one on
    the draft, so asking was a question with no consequence.
    """
    author = actor_name(request, fallback="") or "web"
    if not name.strip():
        return HTMLResponse(
            '<div class="text-red-600 text-sm">템플릿 이름은 필수입니다</div>',
            status_code=400,
        )
    with SessionLocal() as session:
        tpl = EmailTemplate(
            key=_generate_key(session, name.strip()),
            name=name.strip(),
            language="all",
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
    subject: str = Form(""),
):
    """Update an email template, snapshotting the prior state into history.

    ``key``, ``description`` and ``status`` are deliberately absent. The key is a code
    reference the send path resolves and must not move; the description was a field
    nothing ever displayed; and only ``active`` rows are ever read, so draft/archived
    described a template that exists and does nothing. Saving revives a dormant row
    rather than leaving it unreachable now that nothing can set the value back.

    ``language`` is still posted — ``auto_ack`` and ``auto_ack_en`` really are one mail in
    two languages — but a SIGNATURE is pinned to ``all`` no matter what arrives. The screen
    stopped asking (0063), so anything posted for one is a stale value from a form that no
    longer has that field, and writing it back would resurrect a column the operator can
    neither see nor change.
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
        is_signature = (tpl.key or "").startswith(SIGNATURE_KEY_PREFIX)
        tpl.language = "all" if is_signature else (language.strip() or "all")
        tpl.status = "active"
        tpl.version = (tpl.version or 1) + 1
        tpl.body = body
        # 빈 문자열은 "제목 없음" 입니다 — 접수확인이 RE: 고객 제목으로 돌아갑니다.
        tpl.subject = subject.strip() or None
        session.commit()
    # The version still increments — it orders the revision history — it is just not a
    # number anyone reads off a screen.
    return HTMLResponse('<div class="text-green-600 text-sm font-medium">저장 완료</div>')


@router.delete("/email-templates/{tpl_id}")
async def email_templates_delete(tpl_id: int, request: Request):
    """Delete a SIGNATURE — 일주일 동안 되돌릴 수 있습니다. Nothing else deletes.

    행을 지우지 않습니다. ``status='deleted'`` 로 바꾸고 ``deleted_at`` 을 박으면 읽는 쪽은
    전부 ``status='active'`` 만 보므로 발송·고르개에서 즉시 빠지고, 목록에는 흐리게 남아
    되돌릴 수 있습니다. 일주일 뒤 청소됩니다 — src/db/soft_delete.py.


    EVERY signature deletes now, including ``signature_ko``/``signature_en``: since 0061
    no code reads a signature by name — the operator picks one on the draft and presses
    발송 — so a signature is data, and data you cannot delete is a bug in the screen.

    Every other row is still a key the code resolves by name — ``auto_ack``,
    ``auto_ack_en``, ``reply_format``, the two links, the two sender names. Deleting one
    does not remove a feature, it removes the answer to a lookup that still happens: an
    acknowledgement that silently falls back to a hardcoded string, a reply ending on a
    literal ``{{MEETING_LINK}}``, an English mail introducing the writer as "배운태".

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
        tpl.status = DELETED
        tpl.deleted_at = utcnow()
        session.commit()
    return HTMLResponse(
        '<div class="text-orange-600 text-sm font-medium">삭제 완료</div>'
        '<script>setTimeout(()=>location.href="/email-templates",500)</script>'
    )


@router.post("/email-templates/{tpl_id}/restore")
async def email_templates_restore(tpl_id: int, request: Request):
    """되돌리기. 보관 기간 안이면 지우기 전 그대로 돌아옵니다."""
    editor = actor_name(request, fallback="web") or "web"
    with SessionLocal() as session:
        tpl = session.get(EmailTemplate, tpl_id)
        if not tpl:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">보관 기간이 지나 이미 사라졌습니다</div>',
                status_code=404,
            )
        tpl.status = "active"
        tpl.deleted_at = None
        _snapshot_revision(session, tpl, change_note="restored", edited_by=editor)
        session.commit()
    return HTMLResponse('<div class="text-green-600 text-sm font-medium">되돌렸습니다</div>')


