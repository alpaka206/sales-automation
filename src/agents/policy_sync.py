"""``policy_sources`` 의 사본을 초안이 읽는 테이블에 맞춰 두는 곳.

한때 이 파일의 절반은 노션에서 페이지를 읽어 오는 코드였습니다. 그 경로가 전부 막혀서
(토큰 발급 불가 · 쿠키 403 · Export zip 이 부모 한 장) 사람이 콘솔에 붙여넣게 되었고,
읽어 오는 절반은 사라졌습니다. 남은 것은 **한 방향으로 밀어 넣는 일**뿐입니다:

    policy_sources (원본)  ──▶  knowledge_documents (문서 라우터가 읽는 사본)

``mode='knowledge'`` 행만 사본이 됩니다. ``mode='rules'`` 행은 ``llm.prompts._rules_from_db``
가 ``policy_sources`` 에서 직접 읽으므로 사본이 필요 없습니다.

노션에서 자동으로 못 가져오는 이유: docs/정책문서-동기화-설계.md
"""

from __future__ import annotations

import logging
import re
from ..db.models import KnowledgeDocument, PolicySource
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

# How much of the page becomes the router's summary. The router reads slug+title+summary
# for every candidate doc in one prompt, so this stays small on purpose.
_SUMMARY_CHARS = 400


def _slug_for(source: PolicySource) -> str:
    """Stable knowledge slug for a document.

    Derived from ``doc_key``, not the label: renaming "가격 정책" to "B2B 가격 정책" must
    update the same knowledge row, not orphan the old one and leave a second copy the
    router can then cite twice.

    The ``notion-`` prefix is history, kept because changing it would orphan every row
    already stored under it. It names nothing now.
    """
    return f"notion-{source.doc_key[:12]}"


def _summarize(markdown: str) -> str:
    text = re.sub(r"[#>|*`-]", " ", markdown)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_SUMMARY_CHARS]


def _summary_for(source: PolicySource, markdown: str) -> str:
    """라우터가 읽을 한 줄 — 사람이 적은 "언제 쓰는가" 가 있으면 그것.

    없으면 예전처럼 본문 앞부분을 자릅니다. 본문 첫 문단이 용도를 설명하는 문서라면 그것도
    맞는 답이고, 그렇지 않은 문서(바로 표로 시작하는 것들)는 이 칸을 채워야 골라집니다.
    """
    note = (source.usage_note or "").strip()
    return note[:_SUMMARY_CHARS] if note else _summarize(markdown)


# 메일 제목을 사본까지 나르는 방법. KnowledgeDocument 에 열을 하나 더 만드는 것보다 작고,
# 라우터가 보는 인덱스에도 그대로 보입니다.
SUBJECT_TAG = "subject:"


def _tags_for(source: PolicySource) -> list[str]:
    tags = ["notion"]
    subject = (source.subject or "").strip()
    if subject:
        tags.append(f"{SUBJECT_TAG}{subject}")
    return tags


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
            summary=_summary_for(source, markdown),
            author="notion-sync",
            # "all" — 유형 하나가 아니라 **모든** 유형에 후보라는 뜻입니다. 예전에는
            # "policy" 였는데, 그건 문의 유형(sales/support/spam…) 어디와도 안 맞습니다.
            # 평소엔 모델이 골라 주니 티가 안 나지만, 라우터가 실패해서 유형 매칭으로
            # 떨어지는 순간 후보가 0개가 되어 **문서 없이** 답을 씁니다 — 로그 한 줄만 남고.
            categories=["all"],
            tags=_tags_for(source),
        )
        session.add(doc)
        logger.info("Policy sync: created knowledge document %s", slug)
        return
    # 제목과 요약은 본문이 그대로여도 따라가야 합니다 — 제목만 고친 경우, 그리고 "언제
    # 쓰는가" 만 고친 경우가 바로 그 경우입니다. 요약은 라우터가 읽는 유일한 설명이라, 안
    # 따라가면 화면에는 새 용도가 보이는데 문서는 계속 안 골라집니다.
    doc.tags = _tags_for(source)
    summary = _summary_for(source, markdown)
    if (
        (doc.body or "") == markdown
        and doc.title == (title or source.label)
        and (doc.summary or "") == summary
    ):
        return
    doc.title = title or source.label
    doc.body = markdown
    doc.summary = summary
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
