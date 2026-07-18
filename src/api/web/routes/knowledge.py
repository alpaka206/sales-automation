"""Operator-managed policy documents used when drafting inbound replies."""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ....db.models import KnowledgeDocument, KnowledgeDocumentRevision
from ....db.session import SessionLocal
from ....llm.knowledge import reset_cache
from ..auth import actor_name
from ._shared import templates

router = APIRouter(tags=["web"])

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_ALLOWED_SCOPES = {"inbound", "both"}
_ALLOWED_STATUSES = {"active", "inactive"}


@dataclass(frozen=True)
class _DocumentInput:
    title: str
    slug: str
    categories: list[str]
    tags: list[str]
    summary: str
    scope: str
    status: str
    body: str


def _split_csv(value: str) -> list[str]:
    return list(dict.fromkeys(part.strip().lower() for part in value.split(",") if part.strip()))


def _validate(
    *,
    title: str,
    slug: str,
    categories: str,
    tags: str,
    summary: str,
    scope: str,
    status: str,
    body: str,
) -> tuple[_DocumentInput | None, list[str]]:
    clean = _DocumentInput(
        title=title.strip(),
        slug=slug.strip().lower(),
        categories=_split_csv(categories),
        tags=_split_csv(tags),
        summary=summary.strip(),
        scope=scope.strip().lower(),
        status=status.strip().lower(),
        body=body.strip(),
    )
    errors: list[str] = []
    if not clean.title:
        errors.append("문서 제목을 입력하세요.")
    elif len(clean.title) > 160:
        errors.append("문서 제목은 160자 이하여야 합니다.")
    if not _SLUG_RE.fullmatch(clean.slug):
        errors.append("문서 키는 영문 소문자, 숫자, 하이픈(-), 밑줄(_)만 사용할 수 있습니다.")
    elif len(clean.slug) > 80:
        errors.append("문서 키는 80자 이하여야 합니다.")
    if clean.scope not in _ALLOWED_SCOPES:
        errors.append("적용 범위가 올바르지 않습니다.")
    if clean.status not in _ALLOWED_STATUSES:
        errors.append("문서 상태가 올바르지 않습니다.")
    if not clean.body:
        errors.append("정책 본문을 입력하세요.")
    elif len(clean.body) > 100_000:
        errors.append("정책 본문은 100,000자 이하여야 합니다.")
    if len(clean.summary) > 1_000:
        errors.append("AI 선택 요약은 1,000자 이하여야 합니다.")
    if any(len(value) > 80 for value in clean.categories + clean.tags):
        errors.append("분류와 태그의 각 항목은 80자 이하여야 합니다.")
    return (None, errors) if errors else (clean, [])


def _form_value(doc: KnowledgeDocument | _DocumentInput | None) -> dict:
    if doc is None:
        return {
            "title": "",
            "slug": "",
            "categories_text": "all",
            "tags_text": "",
            "summary": "",
            "scope": "inbound",
            "status": "active",
            "body": "",
            "version": 1,
        }
    categories = getattr(doc, "categories", []) or []
    tags = getattr(doc, "tags", []) or []
    return {
        "id": getattr(doc, "id", None),
        "title": doc.title,
        "slug": doc.slug,
        "categories_text": ", ".join(categories),
        "tags_text": ", ".join(tags),
        "summary": doc.summary or "",
        "scope": doc.scope,
        "status": doc.status,
        "body": doc.body,
        "version": getattr(doc, "version", 1) or 1,
    }


def _snapshot(
    session,
    doc: KnowledgeDocument,
    *,
    change_note: str,
    edited_by: str,
) -> None:
    session.add(
        KnowledgeDocumentRevision(
            document_id=doc.id,
            slug=doc.slug,
            version=doc.version or 1,
            title=doc.title,
            categories=doc.categories or [],
            tags=doc.tags or [],
            summary=doc.summary,
            scope=doc.scope,
            body=doc.body,
            author=doc.author,
            status=doc.status,
            change_note=change_note,
            edited_by=edited_by,
        )
    )


def _render_form(
    request: Request,
    *,
    doc: KnowledgeDocument | _DocumentInput | None,
    mode: str,
    errors: list[str] | None = None,
    status_code: int = 200,
    doc_id: int | None = None,
    version: int | None = None,
):
    value = _form_value(doc)
    if doc_id is not None:
        value["id"] = doc_id
    if version is not None:
        value["version"] = version
    return templates.TemplateResponse(
        request,
        "knowledge_form.html",
        {"doc": value, "mode": mode, "errors": errors or []},
        status_code=status_code,
    )


@router.get("/knowledge")
async def knowledge_list(request: Request, status: str = "all"):
    selected_status = status if status in _ALLOWED_STATUSES else "all"
    with SessionLocal() as session:
        query = session.query(KnowledgeDocument)
        if selected_status == "active":
            query = query.filter(KnowledgeDocument.status == selected_status)
        elif selected_status == "inactive":
            query = query.filter(KnowledgeDocument.status != "active")
        rows = query.order_by(KnowledgeDocument.updated_at.desc()).all()
        items = [_form_value(row) | {"updated_at": row.updated_at} for row in rows]
        active_count = (
            session.query(KnowledgeDocument).filter(KnowledgeDocument.status == "active").count()
        )
        inactive_count = (
            session.query(KnowledgeDocument).filter(KnowledgeDocument.status != "active").count()
        )
    return templates.TemplateResponse(
        request,
        "knowledge_list.html",
        {
            "documents": items,
            "filter_status": selected_status,
            "active_count": active_count,
            "inactive_count": inactive_count,
        },
    )


@router.get("/knowledge/new")
async def knowledge_new(request: Request):
    return _render_form(request, doc=None, mode="create")


@router.post("/knowledge")
async def knowledge_create(
    request: Request,
    title: str = Form(""),
    slug: str = Form(""),
    categories: str = Form(""),
    tags: str = Form(""),
    summary: str = Form(""),
    scope: str = Form("inbound"),
    status: str = Form("active"),
    body: str = Form(""),
):
    clean, errors = _validate(
        title=title,
        slug=slug,
        categories=categories,
        tags=tags,
        summary=summary,
        scope=scope,
        status=status,
        body=body,
    )
    if errors:
        submitted = _DocumentInput(
            title=title.strip(),
            slug=slug.strip(),
            categories=_split_csv(categories),
            tags=_split_csv(tags),
            summary=summary.strip(),
            scope=scope,
            status=status,
            body=body,
        )
        return _render_form(request, doc=submitted, mode="create", errors=errors, status_code=422)
    assert clean is not None
    author = actor_name(request, fallback="web") or "web"
    with SessionLocal() as session:
        if session.query(KnowledgeDocument).filter_by(slug=clean.slug).first():
            return _render_form(
                request,
                doc=clean,
                mode="create",
                errors=["이미 사용 중인 문서 키입니다."],
                status_code=409,
            )
        doc = KnowledgeDocument(
            title=clean.title,
            slug=clean.slug,
            categories=clean.categories,
            tags=clean.tags,
            summary=clean.summary or None,
            scope=clean.scope,
            status=clean.status,
            body=clean.body,
            author=author,
            version=1,
        )
        session.add(doc)
        session.flush()
        _snapshot(session, doc, change_note="문서 생성", edited_by=author)
        session.commit()
        doc_id = doc.id
    reset_cache()
    return RedirectResponse(f"/knowledge/{doc_id}?saved=created", status_code=303)


@router.get("/knowledge/{doc_id}")
async def knowledge_edit(request: Request, doc_id: int):
    with SessionLocal() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="정책 문서를 찾을 수 없습니다.")
        return _render_form(request, doc=doc, mode="edit")


@router.post("/knowledge/{doc_id}")
async def knowledge_update(
    request: Request,
    doc_id: int,
    title: str = Form(""),
    categories: str = Form(""),
    tags: str = Form(""),
    summary: str = Form(""),
    scope: str = Form("inbound"),
    status: str = Form("active"),
    body: str = Form(""),
    change_note: str = Form(""),
    expected_version: int = Form(1),
):
    with SessionLocal() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="정책 문서를 찾을 수 없습니다.")
        clean, errors = _validate(
            title=title,
            slug=doc.slug,
            categories=categories,
            tags=tags,
            summary=summary,
            scope=scope,
            status=status,
            body=body,
        )
        if errors:
            submitted = _DocumentInput(
                title=title.strip(),
                slug=doc.slug,
                categories=_split_csv(categories),
                tags=_split_csv(tags),
                summary=summary.strip(),
                scope=scope,
                status=status,
                body=body,
            )
            return _render_form(
                request,
                doc=submitted,
                mode="edit",
                errors=errors,
                status_code=422,
                doc_id=doc.id,
                version=doc.version or 1,
            )
        if (doc.version or 1) != expected_version:
            return _render_form(
                request,
                doc=doc,
                mode="edit",
                errors=["다른 사용자가 먼저 수정했습니다. 최신 내용을 확인한 뒤 다시 저장하세요."],
                status_code=409,
            )
        assert clean is not None
        author = actor_name(request, fallback="web") or "web"
        _snapshot(
            session,
            doc,
            change_note=change_note.strip()[:500] or "내용 수정",
            edited_by=author,
        )
        doc.title = clean.title
        doc.categories = clean.categories
        doc.tags = clean.tags
        doc.summary = clean.summary or None
        doc.scope = clean.scope
        doc.status = clean.status
        doc.body = clean.body
        doc.author = author
        doc.version = (doc.version or 1) + 1
        session.commit()
    reset_cache()
    return RedirectResponse(f"/knowledge/{doc_id}?saved=updated", status_code=303)


@router.get("/knowledge/{doc_id}/history")
async def knowledge_history(request: Request, doc_id: int):
    with SessionLocal() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="정책 문서를 찾을 수 없습니다.")
        current = _form_value(doc)
        rows = (
            session.query(KnowledgeDocumentRevision)
            .filter(KnowledgeDocumentRevision.document_id == doc_id)
            .order_by(
                KnowledgeDocumentRevision.version.desc(),
                KnowledgeDocumentRevision.created_at.desc(),
            )
            .all()
        )
        revisions = [
            {
                "version": row.version,
                "title": row.title,
                "categories_text": ", ".join(row.categories or []),
                "tags_text": ", ".join(row.tags or []),
                "summary": row.summary or "",
                "scope": row.scope,
                "status": row.status,
                "body": row.body,
                "change_note": row.change_note or "",
                "edited_by": row.edited_by or row.author or "",
                "created_at": row.created_at,
            }
            for row in rows
        ]
    return templates.TemplateResponse(
        request,
        "knowledge_history.html",
        {"doc": current, "revisions": revisions},
    )
