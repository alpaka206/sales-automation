"""정책 문서 — 초안이 읽는 문서의 등록부이자, 그 문서를 넣고 고치는 곳.

문서가 들어오는 길은 셋이고, 저장되는 곳은 하나입니다:

    노션 Export zip 드롭   여러 문서를 한 번에. 원본은 노션
    제목+본문 붙여넣기      한 문서를. 원본은 여기 (zip 을 만들 일이 아닐 때)
    본문 편집              이미 있는 문서를

한동안 본문이 읽기 전용이었습니다 — 원본이 노션이라 여기서 고치면 다음 업로드가 덮어쓰기
때문입니다. 그건 지금도 사실이라, 막는 대신 ``edited_at`` 을 남겨 **화면이 그렇게 말합니다.**
조용히 사라지는 것이 문제이지 덮어쓰는 것 자체가 문제는 아닙니다.

어떤 문의에 어떤 문서를 쓸지는 여기서 정하지 않습니다. 모델이 문서 목록을 보고 고릅니다 —
정책도 문서 이름도 바뀌므로, 그 매핑을 코드나 등록부에 굳히면 바뀔 때마다 조용히 끊깁니다.

Every row shows when it was last read and, if the last read failed, what went wrong while
still using the previous copy. An operator must be able to see "정책이 3일째 갱신되지
않았다" without opening the server logs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

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
                "edited_at": s.edited_at,
                "last_synced_at": s.last_synced_at,
                "last_error": s.last_error,
                # A file-imported row has no Notion page yet; the screen says so rather
                # than showing it as a page that simply never synced.
                "from_file": not (s.notion_url or "").strip(),
            }
            for s in sources
        ]


def _page_id_for_pasted(title: str) -> str:
    """노션에서 오지 않은 문서의 id. 제목에서 만들어 냅니다.

    ``notion_page_id`` 는 NOT NULL UNIQUE 이고 등록부의 신원입니다. 붙여넣어 만든 문서에는
    노션 페이지가 없으므로 제목으로 만들되, **안정적으로** 만듭니다 — 같은 제목으로 다시
    만들면 새 행이 아니라 충돌이 되어야, 같은 문서가 둘로 갈라져 라우터가 한 정책을 두 번
    인용하는 일이 없습니다.
    """
    import hashlib

    return hashlib.sha256(title.strip().lower().encode("utf-8")).hexdigest()[:32]


@router.post("/policy-docs")
async def policy_docs_create(
    request: Request,
    label: str = Form(...),
    body: str = Form(""),
    mode: str = Form("knowledge"),
):
    """제목과 본문을 붙여넣어 문서를 하나 만듭니다.

    zip 을 만들기 귀찮을 때의 경로입니다. 한때 여기 있던 "노션 URL 등록" 폼과는 다릅니다 —
    그건 URL만 받고 본문을 가져올 수단이 없어서 영원히 빈 행을 만들었습니다(설계 문서 §2④).
    이건 본문을 같이 받으므로 만든 즉시 초안이 읽을 수 있습니다.
    """
    if not admin_required(request):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")

    label = label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="문서 이름을 입력해 주세요")
    if mode not in _MODE_KEYS:
        mode = "knowledge"

    page_id = _page_id_for_pasted(label)
    with SessionLocal() as session:
        if (
            session.query(PolicySource)
            .filter(PolicySource.notion_page_id == page_id)
            .one_or_none()
            is not None
        ):
            raise HTTPException(status_code=400, detail="같은 이름의 문서가 이미 있습니다")
        source = PolicySource(
            label=label,
            title=label,
            # 노션에서 오지 않았습니다. 빈 URL 이 곧 "동기화 대상 아님" 이라 업로드가
            # 이 문서를 건드리지 않습니다.
            notion_url="",
            notion_page_id=page_id,
            mode=mode,
            body=body,
            edited_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(source)
        session.commit()
        source_id = source.id

    _publish(source_id)
    return {"id": source_id}


@router.put("/policy-docs/{source_id}")
async def policy_docs_update(
    source_id: int,
    request: Request,
    label: str = Form(""),
    body: str = Form(""),
    mode: str = Form(""),
):
    """본문을 고칩니다. 어떤 문서든 고칠 수 있습니다.

    노션에서 온 문서를 여기서 고치면 **같은 문서를 다시 업로드하는 순간 파일 내용으로
    돌아갑니다.** 그게 문제인 것이 아니라 조용히 그러는 것이 문제라서, ``edited_at`` 을
    남기고 화면이 그렇게 말합니다. 노션이 원본인 문서는 노션에서 고치는 편이 낫습니다.
    """
    if not admin_required(request):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")

    with SessionLocal() as session:
        source = session.get(PolicySource, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")
        if label.strip():
            source.label = label.strip()
            source.title = label.strip()
        if mode in _MODE_KEYS:
            source.mode = mode
        if body.strip():
            source.body = body
            source.edited_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()

    _publish(source_id)
    return {"ok": True}


def _publish(source_id: int) -> None:
    """등록부에서 바뀐 것을 초안이 읽는 사본까지 밀어 넣습니다."""
    from ...agents.policy_sync import refresh_knowledge_copy

    try:
        refresh_knowledge_copy(source_id)
    except Exception:
        logger.warning("Knowledge copy refresh failed for %s.", source_id, exc_info=True)


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
