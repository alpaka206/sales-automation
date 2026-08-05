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


def refresh_knowledge_copy(source_id: int) -> None:
    """콘솔에서 본문이나 유형을 바꾼 직후, 초안이 읽는 사본까지 그 자리에서 맞춥니다.

    없으면 "콘솔에서 고쳤는데 다음 업로드 전까지 회신은 예전 내용으로 나가는" 상태가 됩니다 —
    화면에는 바뀐 것이 보이므로 눈치채기 어려운 종류입니다.
    """
    with SessionLocal() as session:
        source = session.get(PolicySource, source_id)
        if source is None or source.mode != "knowledge":
            return
        body = (source.body or "").strip()
        if not body:
            return
        _upsert_knowledge(session, source, source.title or source.label, source.body)
        session.commit()

    from ..llm.knowledge import reset_cache

    reset_cache()


def sync_policy_sources(
    only_id: int | None = None,
    fetcher: Callable[[str], "NotionPage"] | None = None,
) -> dict[str, int]:
    """Read every active registered page and refresh its stored copy.

    Returns counts for the console: ``synced`` pages that changed or were read cleanly,
    ``failed`` pages that kept their previous copy, ``skipped`` file-imported rows that
    have no Notion URL yet.

    ``fetcher`` takes a Notion URL and returns a rendered page. Nothing passes one today:
    every alternative reader (browser cookie, Export zip, local runner) is gone, and the
    only remaining automatic path is ``notion.fetch_page`` — which needs a token this
    workspace cannot issue, so in practice this function no-ops and the console's pasted
    copies stand. The seam stays because that token is the one condition that would make
    all of this unnecessary; see docs/정책문서-동기화-설계.md §5.

    Rows with no ``notion_url`` — i.e. everything pasted into the console — are skipped,
    so a token appearing later refreshes the Notion-backed rows without touching them.
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
            # 파일이 원본으로 돌아왔습니다. 콘솔에서 고쳤던 표시를 지워야 화면이 "콘솔에서
            # 수정함" 이라고 계속 말하지 않습니다 — 실제로는 지금 덮어썼으니까요.
            source.edited_at = None
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
