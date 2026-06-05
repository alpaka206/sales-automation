"""Knowledge base CRUD web routes + revision history."""

from __future__ import annotations

import re

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..auth import actor_name

from ....db.models import KnowledgeDocument, KnowledgeDocumentRevision
from ....db.session import SessionLocal
from ....llm.knowledge import reset_cache as _reset_kb_cache
from ._shared import templates

router = APIRouter(tags=["web"])


def _slugify(title: str) -> str:
    """Simple slug from Korean/English title."""
    slug = title.lower().strip().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9가-힣\-]", "", slug)
    return slug or "untitled"


def _parse_csv(value: str) -> list[str] | None:
    """'a, b ,c' → ['a','b','c']; empty → None."""
    items = [v.strip() for v in (value or "").split(",") if v.strip()]
    return items or None


def _snapshot_revision(session, doc: KnowledgeDocument, change_note: str, edited_by: str) -> None:
    """Append the document's CURRENT state to the revision history."""
    session.add(
        KnowledgeDocumentRevision(
            document_id=doc.id,
            slug=doc.slug,
            version=doc.version or 1,
            title=doc.title,
            categories=doc.categories,
            tags=doc.tags,
            summary=doc.summary,
            scope=doc.scope,
            body=doc.body,
            author=doc.author,
            status=doc.status or "active",
            change_note=change_note,
            edited_by=edited_by,
        )
    )


@router.get("/knowledge")
async def knowledge_list(request: Request):
    """List all knowledge base documents."""
    with SessionLocal() as session:
        docs = (
            session.query(KnowledgeDocument)
            .order_by(KnowledgeDocument.updated_at.desc())
            .all()
        )
        items = [
            {
                "id": d.id,
                "title": d.title,
                "slug": d.slug,
                "categories": d.categories or [],
                "scope": d.scope,
                "status": d.status or "active",
                "version": d.version or 1,
                "updated_at": d.updated_at,
            }
            for d in docs
        ]
    return templates.TemplateResponse(request, "knowledge_list.html", {"docs": items})


@router.get("/knowledge/new")
async def knowledge_new(request: Request):
    """Form to create a new knowledge document."""
    return templates.TemplateResponse(request, "knowledge_form.html", {
        "doc": None, "mode": "create",
    })


@router.get("/knowledge/{doc_id}")
async def knowledge_edit(request: Request, doc_id: int):
    """Edit form for an existing knowledge document."""
    with SessionLocal() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")
        item = {
            "id": doc.id,
            "title": doc.title,
            "slug": doc.slug,
            "categories": ",".join(doc.categories) if doc.categories else "",
            "tags": ",".join(doc.tags) if doc.tags else "",
            "summary": doc.summary or "",
            "scope": doc.scope,
            "author": doc.author or "",
            "status": doc.status or "active",
            "version": doc.version or 1,
            "body": doc.body,
        }
    return templates.TemplateResponse(request, "knowledge_form.html", {
        "doc": item, "mode": "edit",
    })


@router.post("/knowledge")
async def knowledge_create(
    request: Request,
    title: str = Form(""),
    categories: str = Form(""),
    tags: str = Form(""),
    summary: str = Form(""),
    scope: str = Form("both"),
    author: str = Form(""),
    status: str = Form("active"),
    body: str = Form(""),
):
    """Create a new knowledge document and record its first revision."""
    # Attribute to the logged-in user (Google OAuth) when available, else the form/typed value.
    author = actor_name(request, fallback=author.strip()) or "web"
    if not title.strip() or not body.strip():
        return HTMLResponse(
            '<div class="text-red-600 text-sm">제목과 본문은 필수입니다</div>',
            status_code=400,
        )
    slug = _slugify(title.strip())
    with SessionLocal() as session:
        existing = session.query(KnowledgeDocument).filter_by(slug=slug).first()
        if existing:
            slug = f"{slug}-{existing.id + 1}"
        doc = KnowledgeDocument(
            title=title.strip(),
            slug=slug,
            categories=_parse_csv(categories),
            tags=_parse_csv(tags),
            summary=summary.strip() or None,
            scope=scope,
            author=author.strip() or None,
            status=status.strip() or "active",
            version=1,
            body=body.strip(),
        )
        session.add(doc)
        session.flush()
        _snapshot_revision(session, doc, change_note="created", edited_by=author.strip() or "web")
        session.commit()
    _reset_kb_cache()
    return HTMLResponse(
        '<div class="text-green-600 text-sm font-medium">문서 생성 완료</div>'
        '<script>setTimeout(()=>location.href="/knowledge",500)</script>'
    )


@router.put("/knowledge/{doc_id}")
async def knowledge_update(
    doc_id: int,
    request: Request,
    title: str = Form(""),
    categories: str = Form(""),
    tags: str = Form(""),
    summary: str = Form(""),
    scope: str = Form("both"),
    author: str = Form(""),
    status: str = Form("active"),
    body: str = Form(""),
    change_note: str = Form(""),
):
    """Update a knowledge document, snapshotting the prior state into history."""
    author = actor_name(request, fallback=author.strip()) or "web"
    with SessionLocal() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        if not doc:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">문서를 찾을 수 없습니다</div>',
                status_code=404,
            )
        # Snapshot the current (pre-edit) state, then bump version and apply.
        _snapshot_revision(
            session,
            doc,
            change_note=change_note.strip() or "edited",
            edited_by=author.strip() or "web",
        )
        if title.strip():
            doc.title = title.strip()
        doc.categories = _parse_csv(categories)
        doc.tags = _parse_csv(tags)
        doc.summary = summary.strip() or None
        doc.scope = scope
        if author.strip():
            doc.author = author.strip()
        doc.status = status.strip() or "active"
        doc.version = (doc.version or 1) + 1
        if body.strip():
            doc.body = body.strip()
        session.commit()
        new_version = doc.version
    _reset_kb_cache()
    return HTMLResponse(
        f'<div class="text-green-600 text-sm font-medium">저장 완료 (v{new_version})</div>'
    )


@router.delete("/knowledge/{doc_id}")
async def knowledge_delete(doc_id: int, request: Request):
    """Delete a knowledge document (keeps its revision history)."""
    editor = actor_name(request, fallback="web") or "web"
    with SessionLocal() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        if not doc:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">문서를 찾을 수 없습니다</div>',
                status_code=404,
            )
        _snapshot_revision(session, doc, change_note="deleted", edited_by=editor)
        session.delete(doc)
        session.commit()
    _reset_kb_cache()
    return HTMLResponse(
        '<div class="text-orange-600 text-sm font-medium">삭제 완료</div>'
        '<script>setTimeout(()=>location.href="/knowledge",500)</script>'
    )


@router.get("/knowledge/{doc_id}/history")
async def knowledge_history(request: Request, doc_id: int):
    """Show the revision history for a knowledge document."""
    with SessionLocal() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        revs = (
            session.query(KnowledgeDocumentRevision)
            .filter_by(document_id=doc_id)
            .order_by(KnowledgeDocumentRevision.created_at.desc())
            .all()
        )
        current = (
            {"id": doc.id, "title": doc.title, "version": doc.version or 1}
            if doc
            else {"id": doc_id, "title": "(삭제됨)", "version": "-"}
        )
        items = [
            {
                "version": r.version,
                "title": r.title,
                "change_note": r.change_note or "",
                "edited_by": r.edited_by or "",
                "status": r.status,
                "summary": r.summary or "",
                "body": r.body,
                "created_at": r.created_at,
            }
            for r in revs
        ]
    return templates.TemplateResponse(
        request, "knowledge_history.html", {"doc": current, "revisions": items}
    )
