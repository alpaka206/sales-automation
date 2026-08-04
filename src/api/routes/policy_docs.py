"""정책 문서 — the operator's registry of Notion pages used as policy.

The console does not edit policy; it points at where policy lives. So this screen is a
list of (label, Notion URL, how it is used) plus a sync button, and the document body is
read-only here — editing happens in Notion, which is the whole point of the feature.

Every row shows when it was last read and, if the last read failed, what went wrong while
still using the previous copy. An operator must be able to see "정책이 3일째 갱신되지
않았다" without opening the server logs.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import RedirectResponse

from ...db.models import PolicySource
from ...db.session import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])

MODES = (
    ("knowledge", "문의별 참고", "문의 내용에 맞으면 답변 작성 시 이 문서를 참고합니다."),
    ("rules", "항상 적용", "모든 답변에 항상 적용되는 규칙입니다(톤·금지사항 등)."),
)
_MODE_KEYS = {key for key, _label, _desc in MODES}


def _rows() -> list[dict]:
    with SessionLocal() as session:
        sources = (
            session.query(PolicySource)
            .order_by(PolicySource.mode, PolicySource.order_index, PolicySource.id)
            .all()
        )
        return [
            {
                "id": s.id,
                "label": s.label,
                "notion_url": s.notion_url,
                "mode": s.mode,
                "status": s.status,
                "order_index": s.order_index,
                "chars": len(s.body or ""),
                "last_synced_at": s.last_synced_at,
                "last_error": s.last_error,
                # A file-imported row has no Notion page yet; the screen says so rather
                # than showing it as a page that simply never synced.
                "from_file": not (s.notion_url or "").strip(),
            }
            for s in sources
        ]


@router.post("/policy-docs")
async def policy_docs_add(
    label: str = Form(...),
    notion_url: str = Form(...),
    mode: str = Form("knowledge"),
):
    from ...integrations.notion import NotionError, page_id_from_url

    label = label.strip()
    notion_url = notion_url.strip()
    if not label or not notion_url:
        raise HTTPException(status_code=400, detail="이름과 노션 링크를 모두 입력해 주세요")
    if mode not in _MODE_KEYS:
        mode = "knowledge"
    try:
        page_id = page_id_from_url(notion_url)
    except NotionError as exc:
        return RedirectResponse(f"/policy-docs?error={exc}", status_code=303)

    with SessionLocal() as session:
        existing = (
            session.query(PolicySource)
            .filter(PolicySource.notion_page_id == page_id)
            .one_or_none()
        )
        if existing is not None:
            # Same page registered twice would let the router cite one document as two.
            return RedirectResponse(
                "/policy-docs?error=이미 등록된 노션 페이지입니다", status_code=303
            )
        session.add(
            PolicySource(
                label=label, notion_url=notion_url, notion_page_id=page_id, mode=mode
            )
        )
        session.commit()
    return RedirectResponse("/policy-docs", status_code=303)


@router.post("/policy-docs/{source_id}/delete")
async def policy_docs_delete(source_id: int):
    with SessionLocal() as session:
        source = session.get(PolicySource, source_id)
        if source is not None:
            session.delete(source)
            session.commit()
    return RedirectResponse("/policy-docs", status_code=303)


@router.post("/policy-docs/{source_id}/toggle")
async def policy_docs_toggle(source_id: int):
    """Pause a document without losing its registration or its synced copy."""
    with SessionLocal() as session:
        source = session.get(PolicySource, source_id)
        if source is not None:
            source.status = "paused" if source.status == "active" else "active"
            session.commit()
    return RedirectResponse("/policy-docs", status_code=303)


@router.post("/policy-docs/sync")
async def policy_docs_sync():
    """Read every registered page now, instead of waiting for the poller tick."""
    import asyncio

    from ...agents.policy_sync import sync_policy_sources

    try:
        result = await asyncio.to_thread(sync_policy_sources)
    except Exception as exc:
        logger.warning("Manual policy sync failed.", exc_info=True)
        return RedirectResponse(f"/policy-docs?error={str(exc)[:150]}", status_code=303)
    return RedirectResponse(
        f"/policy-docs?synced={result['synced']}&failed={result['failed']}", status_code=303
    )
