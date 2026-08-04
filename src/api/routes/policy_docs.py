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

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from ...db.models import PolicySource
from ..auth import admin_required
from ...db.session import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])

# 워크스페이스 전체를 내보내면 커집니다. 서버 메모리에서 읽으므로 상한이 필요하고,
# 25MB 면 정책 문서 수십 페이지에는 충분합니다.
_MAX_EXPORT_BYTES = 25 * 1024 * 1024

MODES = (
    ("knowledge", "문의별 참고"),
    ("rules", "항상 적용"),
)
_MODE_KEYS = {key for key, _label in MODES}


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


@router.post("/policy-docs/upload-export")
async def policy_docs_upload_export(request: Request, export: UploadFile = File(...)):
    """노션 Export zip 을 올려 등록된 문서를 갱신합니다.

    로컬 실행 스크립트가 왜 대안이 아닌지: 그 스크립트는 노션에서 읽어 **DB에 씁니다.**
    그런데 사내망이 5432/6543 아웃바운드를 막고 있어서 담당자 PC는 DB에 닿지 못합니다.
    노션은 브라우저로 되고 DB는 서버만 되니, 양쪽을 다 할 수 있는 기계가 없습니다.

    zip 을 올리면 그 문제가 사라집니다:

        노션 → (브라우저 Export) → zip → (HTTPS 업로드) → 서버 → DB

    각 구간이 실제로 뚫려 있는 경로만 씁니다. 노션 API 토큰도, 쿠키도, DB 접근도 필요 없고,
    파일을 만드는 사람은 노션을 볼 수 있는 사람이면 됩니다.

    zip 을 저장하지 않습니다 — 메모리에서 읽고 버립니다. 정책 사본은 이미 DB에 있고, 업로드
    파일을 서버에 남기면 지워야 할 사본이 하나 더 생길 뿐입니다.
    """
    if not admin_required(request):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")

    payload = await export.read()
    if not payload:
        raise HTTPException(status_code=400, detail="빈 파일입니다")
    if len(payload) > _MAX_EXPORT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"파일이 너무 큽니다 (최대 {_MAX_EXPORT_BYTES // (1024 * 1024)}MB)",
        )

    import asyncio

    from ...agents.policy_sync import register_export_pages, sync_policy_sources
    from ...integrations.notion import page_id_from_url
    from ...integrations.notion_export import NotionExportError, read_export

    try:
        pages = await asyncio.to_thread(read_export, payload)
    except NotionExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="노션 Markdown & CSV 내보내기 zip 이 아닌 것 같습니다",
        ) from exc

    # 파일이 곧 목록입니다. URL 을 하나씩 손으로 등록하게 하면 노션에서 문서를 만든 사람과
    # 콘솔에 등록하는 사람이 같아야 하고, 한쪽만 하면 조용히 누락됩니다 — 실제로 그렇게
    # 누락돼 있었습니다.
    registered = await asyncio.to_thread(register_export_pages, pages)
    if registered.get("error"):
        raise HTTPException(status_code=400, detail=registered["error"])

    def fetch(url_or_id: str):
        page = pages.get(page_id_from_url(url_or_id))
        if page is None:
            raise NotionExportError(
                "이 내보내기에 없는 문서입니다. 해당 페이지를 포함해 다시 내보내 주세요."
            )
        return page

    result = await asyncio.to_thread(sync_policy_sources, fetcher=fetch)
    result["added"] = registered["added"]
    result["added_labels"] = registered["labels"]
    return result


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
