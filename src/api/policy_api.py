"""정책 문서를 로컬에서 서버로 밀어 넣는 API (내부 토큰 인증).

왜 필요한가 — 어느 기계도 혼자서는 못 합니다:

    담당자 PC → 노션        OK (본인 계정 / 브라우저 Export)
    담당자 PC → DB          차단 (사내망이 5432·6543 아웃바운드를 막음)
    서버       → DB          OK
    서버       → 노션        불가 (회사 워크스페이스라 통합 토큰 발급 불가)

그런데 **담당자 PC → 서버 HTTPS(443)** 는 열려 있습니다. 그래서 노션을 읽는 쪽과 DB에 쓰는
쪽을 그 경로로 잇습니다:

    로컬: 서버에서 등록 목록을 받고  →  노션에서 각 페이지를 읽고  →  서버로 올림
    서버: 받은 내용을 DB에 저장

로컬은 DB를 몰라도 되고, 서버는 노션을 몰라도 됩니다. 저장 로직은
:func:`sync_policy_sources` 하나뿐 — 무엇이 저장되고 무엇이 지식 문서가 되는지가 갈리면
"콘솔로 넣은 것과 스크립트로 넣은 것이 다르다"가 되므로, 여기서는 fetcher만 바꿔 끼웁니다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db.models import PolicySource
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

# /api/ui 와 달리 이 접두사는 web UI 경로가 아니므로 미들웨어의 INTERNAL_API_TOKEN 검사를
# 그대로 받습니다. 브라우저 세션으로는 열리지 않습니다.
router = APIRouter(prefix="/api/policy", tags=["policy-sync"])


class PushedPage(BaseModel):
    """로컬에서 읽어 올린 노션 페이지 하나."""

    notion_url: str
    title: str = ""
    markdown: str


class PushRequest(BaseModel):
    pages: list[PushedPage] = Field(default_factory=list)


@router.get("/sources")
async def list_sources() -> dict:
    """무엇을 읽어 와야 하는지. 로컬 러너가 가장 먼저 묻는 것입니다.

    등록·중지·삭제는 콘솔이 하고 이 목록은 그 결과를 읽기만 합니다 — 읽을 대상을 정하는
    곳이 둘이 되면 화면과 스크립트가 서로 다른 문서를 최신이라고 부르게 됩니다.
    """
    with SessionLocal() as session:
        rows = (
            session.query(PolicySource)
            .filter(PolicySource.status == "active")
            .order_by(PolicySource.order_index, PolicySource.id)
            .all()
        )
        return {
            "sources": [
                {
                    "id": row.id,
                    "label": row.label,
                    "mode": row.mode,
                    "notion_url": row.notion_url or "",
                    "last_synced_at": row.last_synced_at,
                }
                for row in rows
                if (row.notion_url or "").strip()
            ]
        }


@router.post("/push")
async def push_pages(payload: PushRequest) -> dict:
    """로컬에서 읽은 페이지들을 저장합니다. 등록된 URL 만 반영됩니다.

    올린 쪽이 무엇을 저장할지 정하지 않는다는 점이 중요합니다: 본문만 건네고, 어떤 행이
    갱신되는지는 서버의 등록부가 정합니다. 등록되지 않은 페이지를 올려도 무시됩니다.
    """
    from ..agents.policy_sync import sync_policy_sources
    from ..integrations.notion import NotionPage, page_id_from_url

    by_id: dict[str, NotionPage] = {}
    for page in payload.pages:
        try:
            key = page_id_from_url(page.notion_url)
        except Exception:
            # 못 알아보는 URL 은 조용히 넘깁니다 — 등록부에 없으면 어차피 안 쓰입니다.
            continue
        by_id[key] = NotionPage(page_id=key, title=page.title or "", markdown=page.markdown)

    def fetch(url_or_id: str) -> NotionPage:
        page = by_id.get(page_id_from_url(url_or_id))
        if page is None:
            raise LookupError(
                "이번 업로드에 이 페이지가 없습니다. 로컬에서 읽지 못했거나 "
                "내보내기에 포함되지 않았습니다."
            )
        return page

    result = sync_policy_sources(fetcher=fetch)
    logger.info(
        "Policy push: %d page(s) offered, %d synced, %d failed, %d skipped.",
        len(by_id), result["synced"], result["failed"], result["skipped"],
    )
    return result
