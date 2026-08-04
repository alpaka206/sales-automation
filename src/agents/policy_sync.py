"""Pull the registered Notion policy pages into this database.

Direction is one way: Notion → us. Nothing here writes to Notion, and nothing here can
send mail or touch HubSpot, so it is unaffected by the pre-launch guard.

The contract that makes this safe to run on a timer: **a sync failure never removes
policy**. Each row keeps the last copy that was read successfully, and a failure only
writes ``last_error``. So a revoked token, an unshared page or a Notion outage degrades to
"answers use yesterday's policy", never to "answers use no policy" — which is the failure
mode that would put invented numbers in front of a customer.

``mode='knowledge'`` rows are additionally upserted into ``knowledge_documents``, the table
the per-inquiry document router already reads, so registering a page is all it takes for
the drafting prompt to start quoting it. ``mode='rules'`` rows are read straight out of
``policy_sources`` by ``llm.prompts._rules_from_db``.

노션 API 를 쓰지 않는 이유: docs/정책문서-동기화-설계.md
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..db.models import KnowledgeDocument, PolicySource
from ..db.session import SessionLocal

if TYPE_CHECKING:
    from ..integrations.notion import NotionPage

logger = logging.getLogger(__name__)

# How much of the page becomes the router's summary. The router reads slug+title+summary
# for every candidate doc in one prompt, so this stays small on purpose.
_SUMMARY_CHARS = 400


def _slug_for(source: PolicySource) -> str:
    """Stable knowledge slug for a registered page.

    Derived from the page id, not the label: an operator renaming "가격 정책" to
    "B2B 가격 정책" must update the same knowledge row, not orphan the old one and create
    a second copy the router can then cite twice.
    """
    return f"notion-{source.notion_page_id[:12]}"


def _summarize(markdown: str) -> str:
    text = re.sub(r"[#>|*`-]", " ", markdown)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_SUMMARY_CHARS]


def _upsert_knowledge(session, source: PolicySource, title: str, markdown: str) -> None:
    slug = _slug_for(source)
    doc = session.query(KnowledgeDocument).filter(KnowledgeDocument.slug == slug).one_or_none()
    if doc is None:
        doc = KnowledgeDocument(
            slug=slug,
            title=title or source.label,
            body=markdown,
            scope="inbound",
            status="active",
            summary=_summarize(markdown),
            author="notion-sync",
            categories=["policy"],
            tags=["notion"],
        )
        session.add(doc)
        logger.info("Policy sync: created knowledge document %s", slug)
        return
    if (doc.body or "") == markdown and doc.title == (title or source.label):
        return
    doc.title = title or source.label
    doc.body = markdown
    doc.summary = _summarize(markdown)
    doc.status = "active"
    doc.version = (doc.version or 1) + 1
    logger.info("Policy sync: updated knowledge document %s (v%d)", slug, doc.version)


# 한 번 올리는 zip 이 담을 만한 문서 수의 상한. 정책 페이지와 그 자식들은 수십 개면 넉넉하고,
# 워크스페이스 전체를 통째로 내보낸 zip 이 실수로 올라왔을 때 등록부가 수백 줄로 불어나는
# 것을 막습니다. 넘으면 아무것도 추가하지 않고 이유를 말합니다.
MAX_NEW_SOURCES = 60


def register_export_pages(pages: dict) -> dict:
    """내보내기에 있는 페이지를 등록부에 반영합니다 — 있으면 두고, 없으면 추가.

    URL 을 하나씩 손으로 등록하게 하면, 노션에서 자식 문서를 만든 사람과 콘솔에 등록하는
    사람이 같아야 하고 한쪽만 하면 조용히 누락됩니다. 실제로 그렇게 누락돼 있었습니다:
    정책 문서는 등록됐는데 그 아래 지원 언어·크레딧·플랜 기능은 아무도 등록하지 않아서
    AI 가 존재조차 몰랐습니다.

    그래서 파일이 곧 목록입니다. 올린 zip 에 있는 문서는 전부 읽습니다.

    새 문서는 ``knowledge``(문의별 참고)로 들어옵니다. ``rules``(항상 적용)는 모든 회신에
    붙어 다니므로 늘어나면 안 되는 쪽이고, 무엇이 거기 들어갈지는 사람이 정할 일입니다 —
    콘솔에서 바꿀 수 있습니다.

    지우지 않습니다. zip 에 없는 문서를 사라진 것으로 볼 수는 없습니다 — 한 페이지만
    내보낸 zip 을 올렸을 뿐일 수도 있으니까요. 삭제는 콘솔에서 사람이 합니다.
    """
    from ..db.models import PolicySource

    added, existing = [], 0
    with SessionLocal() as session:
        known = {
            row.notion_page_id
            for row in session.query(PolicySource.notion_page_id).all()
        }
        unknown = [pid for pid in pages if pid not in known]
        if len(unknown) > MAX_NEW_SOURCES:
            return {
                "added": 0,
                "existing": len(pages) - len(unknown),
                "error": (
                    f"새 문서가 {len(unknown)}개입니다. 워크스페이스 전체를 내보낸 zip 인 것 "
                    f"같습니다 — 정책 페이지 하나를 'Include subpages' 로 내보내 주세요."
                ),
            }

        for page_id, page in pages.items():
            if page_id in known:
                existing += 1
                continue
            source = PolicySource(
                label=(page.title or page_id)[:200],
                # 내보내기에는 원래 URL 이 없습니다. 페이지 id 로 정규 주소를 만들면
                # page_id_from_url 이 같은 id 로 되돌려 읽으므로 동기화가 이어집니다.
                notion_url=f"https://www.notion.so/{page_id}",
                notion_page_id=page_id,
                mode="knowledge",
                status="active",
            )
            session.add(source)
            added.append(source.label)
        session.commit()

    return {"added": len(added), "existing": existing, "labels": added, "error": None}


def sync_policy_sources(
    only_id: int | None = None,
    fetcher: Callable[[str], "NotionPage"] | None = None,
) -> dict[str, int]:
    """Read every active registered page and refresh its stored copy.

    Returns counts for the console: ``synced`` pages that changed or were read cleanly,
    ``failed`` pages that kept their previous copy, ``skipped`` file-imported rows that
    have no Notion URL yet.

    ``fetcher`` takes a Notion URL and returns a rendered page. It exists because the
    integration API is not available on every workspace — a company workspace where
    nobody can create an internal integration cannot use ``notion.fetch_page`` at all. The
    local runner (``scripts/sync_notion_local.py``) passes a reader that works from the
    operator's own machine instead. Everything downstream of the fetch — what is stored,
    what becomes a knowledge document, what happens on failure — is deliberately the same
    whichever reader ran, so there is one copy of the rules that keep a bad read from
    stripping policy out of a reply.
    """
    from ..integrations import notion

    result = {"synced": 0, "failed": 0, "skipped": 0}
    if fetcher is None:
        if not notion.is_configured():
            logger.info("Policy sync skipped: NOTION_TOKEN is not set.")
            return result
        fetcher = notion.fetch_page

    with SessionLocal() as session:
        query = session.query(PolicySource).filter(PolicySource.status == "active")
        if only_id is not None:
            query = query.filter(PolicySource.id == only_id)
        sources = query.order_by(PolicySource.order_index, PolicySource.id).all()

        for source in sources:
            if not (source.notion_url or "").strip():
                # Imported from a file and not yet pointed at Notion — leave its body be.
                result["skipped"] += 1
                continue
            try:
                page = fetcher(source.notion_url)
            except Exception as exc:
                source.last_error = str(exc)[:500]
                result["failed"] += 1
                logger.warning(
                    "Policy sync failed for %s; keeping the copy from %s.",
                    source.label,
                    source.last_synced_at or "never",
                    exc_info=True,
                )
                continue

            if not page.markdown.strip():
                # An empty render is far more likely to be a parsing problem than a
                # genuinely empty policy page. Refusing it keeps the good copy.
                source.last_error = "노션 페이지에서 읽어온 내용이 비어 있어 이전 사본을 유지했습니다."
                result["failed"] += 1
                continue

            source.body = page.markdown
            source.title = page.title
            source.summary = _summarize(page.markdown)
            source.last_synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
            source.last_error = None
            result["synced"] += 1

            if source.mode == "knowledge":
                _upsert_knowledge(session, source, page.title, page.markdown)

        session.commit()

    if result["synced"] or result["failed"]:
        logger.info(
            "Policy sync: %d synced, %d failed, %d skipped.",
            result["synced"],
            result["failed"],
            result["skipped"],
        )
    return result
