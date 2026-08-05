"""
Knowledge base loader (DB-backed) + LLM document router.

Two ways to pull knowledge into a prompt:

1. ``load_relevant_docs(category, scope)`` — deterministic category matching.
   Fast, no LLM call. Used as the fallback and by code paths that only know a
   category. Caching: ``lru_cache`` keyed on (category, scope).

2. ``select_relevant_docs(inquiry, category, scope, llm)`` — the LLM router.
   Builds a compact index (slug + title + summary + tags + categories) of every
   *active* document and asks Gemini which ones are relevant to the actual
   inquiry text, then loads only those bodies. Falls back to category matching
   when no LLM is supplied, the router errors, or it selects nothing.

Matching rules (category mode):
- ``categories`` JSON list may include ``"all"`` → matches every category
  except ``spam``.
- A document with an empty or NULL ``categories`` list is treated as ``[all]``.
- ``scope`` filtering: ``"inbound"`` matches docs with scope ``inbound`` or
  ``both``. ``scope="both"`` matches everything.
- Only ``status == "active"`` documents are ever returned.

어떤 문의에 어떤 문서를 붙일지는 **코드에 없습니다.** 모델이 목록을 보고 고릅니다 — 정책은
바뀌고 문서 이름도 바뀌므로, 매핑을 코드에 굳히면 그때마다 아무 흔적 없이 끊깁니다.
"""

from __future__ import annotations

import logging
from pydantic import BaseModel

from ..db.models import KnowledgeDocument
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)


class SelectDocsResult(BaseModel):
    """Router output: which document slugs are relevant to the inquiry."""

    slugs: list[str] = []
    reasoning: str = ""


def _scope_matches(doc_scope: str, requested: str) -> bool:
    """Return True if the document scope is compatible with the request."""
    if requested == "both" or doc_scope == "both":
        return True
    return doc_scope == requested


def _category_matches(doc_categories: list | None, category: str) -> bool:
    """Return True if the document is relevant to *category*."""
    if not doc_categories:
        return True
    normalized = {c.lower() for c in doc_categories}
    if "all" in normalized:
        return True
    return category.lower() in normalized


def _is_active(doc: KnowledgeDocument) -> bool:
    """Treat a missing/blank status as active (back-compat with old rows)."""
    return (getattr(doc, "status", None) or "active") == "active"


# 정책 문서가 들고 온 메일 제목은 ``tags`` 에 "subject:<제목>" 으로 실려 옵니다
# (agents/policy_sync._tags_for). 여기서 다시 꺼냅니다.
_SUBJECT_TAG = "subject:"


def _subject_of(doc: KnowledgeDocument) -> str | None:
    for tag in doc.tags or []:
        if isinstance(tag, str) and tag.startswith(_SUBJECT_TAG):
            return tag[len(_SUBJECT_TAG) :].strip() or None
    return None


def subject_from_docs(docs: list[KnowledgeDocument]) -> str | None:
    """A mail subject carried by one of the documents the draft was written from.

    Read in CODE, never asked of the model — the same reason CODE GUARD 3 exists. A
    subject is exactly the kind of short line a model will happily invent, and then RE:
    stacks or the language flips.

    **메일 제목은 메일 템플릿에만 채웁니다.** 지원 언어·크레딧 같은 근거 문서는 내용을
    제공할 뿐 그 메일의 제목을 정하지 않습니다 — 그 문서들의 제목 칸은 비워 둡니다. 코드가
    이름으로 "메일 템플릿" 을 알아보게 하지는 않았습니다: 문서 이름은 바뀌고, 이름을 조건에
    넣으면 이름을 바꾸는 순간 조용히 끊깁니다(오늘만 두 번 지운 실패 방식입니다).

    그래서 규칙은 "제목을 채운 문서가 정한다" 이고, 둘 이상이 채워져 있으면 **경고를 남기고**
    첫 번째를 씁니다. 조용히 하나를 고르면 고객 메일함에 뜨는 제목이 문서 제목 알파벳순으로
    정해지고, 왜 그런지 아무 데도 안 남습니다.
    """
    with_subject = [(doc, _subject_of(doc)) for doc in docs]
    carrying = [(doc, subject) for doc, subject in with_subject if subject]
    if not carrying:
        return None
    if len(carrying) > 1:
        logger.warning(
            "%d documents carry a mail subject; using %s. 메일 제목은 메일 템플릿에만 "
            "채우고 근거 문서(%s)는 비워 두세요.",
            len(carrying),
            carrying[0][0].title,
            ", ".join(doc.title for doc, _ in carrying[1:]),
        )
    return carrying[0][1]


def _format_docs(docs: list[KnowledgeDocument]) -> str:
    """Render selected documents as a prompt-ready block."""
    parts = [f"### {doc.title}\n{doc.body}" for doc in docs]
    if not parts:
        return ""
    return "## Relevant knowledge base documents\n\n" + "\n\n---\n\n".join(parts)


def _matching_docs(category: str, scope: str) -> list[KnowledgeDocument]:
    """Category-mode matches. Not cached: the rows are few and a stale cache here is the
    difference between yesterday's policy and today's."""
    if not category:
        return []
    session = SessionLocal()
    try:
        docs = session.query(KnowledgeDocument).order_by(KnowledgeDocument.title).all()
    finally:
        session.close()
    return [
        doc
        for doc in docs
        if _is_active(doc)
        and _category_matches(doc.categories, category)
        and _scope_matches(doc.scope, scope)
    ]


def reset_cache() -> None:
    """Invalidate cached knowledge-doc queries AND the company-rules prompt cache.

    Used by tests and by the web UI after a knowledge edit, so an operator who
    also edited a policy document doesn't keep seeing a stale copy until restart.
    """
    from .prompts import get_company_rules

    get_company_rules.cache_clear()


def load_relevant_docs(category: str, scope: str = "inbound") -> str:
    """
    Return a formatted string of all knowledge documents matching *category* and *scope*.

    ``spam`` used to short-circuit to "". It no longer does: 영업·홍보 목적의 문의에도
    회신은 나가고, 그 회신이 볼 것이 소개 문서입니다. 문서를 빼앗으면 그 회신만 아무 근거
    없이 쓰이게 됩니다.
    """
    return _format_docs(_matching_docs(category, scope))


# --------------------------------------------------------------------------- #
# LLM document router
# --------------------------------------------------------------------------- #


def _candidate_docs(scope: str) -> list[KnowledgeDocument]:
    """Active documents compatible with *scope*, ordered by title."""
    session = SessionLocal()
    try:
        docs = session.query(KnowledgeDocument).order_by(KnowledgeDocument.title).all()
    finally:
        session.close()
    return [d for d in docs if _is_active(d) and _scope_matches(d.scope, scope)]


def _build_index(docs: list[KnowledgeDocument]) -> str:
    """Compact, token-cheap index the router reads to pick documents."""
    lines: list[str] = []
    for doc in docs:
        cats = ", ".join(doc.categories or []) or "all"
        tags = ", ".join(doc.tags or []) or "-"
        summary = (doc.summary or "").strip() or "(no summary)"
        lines.append(
            f"- slug: {doc.slug}\n"
            f"  title: {doc.title}\n"
            f"  categories: {cats}\n"
            f"  tags: {tags}\n"
            f"  summary: {summary}"
        )
    return "\n".join(lines)


def select_relevant_docs(
    inquiry: str,
    category: str,
    scope: str = "inbound",
    llm: object | None = None,
    language: str | None = None,
    with_subject: bool = False,
):
    """어떤 문서를 보고 답할지 **모델이** 고릅니다.

    유형별로 볼 문서를 코드에 적지 않습니다. 정책은 바뀌고, 문서는 노션에서 이름이 바뀌고,
    새 문서는 아무 코드도 모르는 채로 들어옵니다 — 매핑을 코드에 굳히면 그때마다 조용히
    끊깁니다. 대신 모델에게 문서 목록(제목·요약·태그)과 문의를 주고 고르게 합니다.

    ``category`` 는 힌트로 넘어가고, ``language`` 도 마찬가지입니다: 같은 문서가 KR/ENG 두
    벌로 있으면 문의 언어에 맞는 쪽만 고르라고 프롬프트가 말합니다(둘 다 넣으면 따라야 할
    형식이 두 개가 됩니다). 규칙이 프롬프트에 있다는 것이 요점입니다 — 정책이 바뀌면 문서와
    프롬프트가 바뀌지, 라우팅 표를 고치러 코드로 오지 않습니다.

    라우터가 실패하거나 아무것도 못 고르면 유형 매칭으로 떨어집니다.

    ``with_subject=True`` 면 (본문, 그 문서들이 들고 온 메일 제목) 을 돌려줍니다. 제목이
    필요한 곳은 초안 한 군데뿐이라 기본값은 예전 그대로 문자열입니다.
    """
    if not category:
        return ("", None) if with_subject else ""

    def done(docs: list[KnowledgeDocument] | None, formatted: str | None = None):
        text = _format_docs(docs) if formatted is None else formatted
        return (text, subject_from_docs(docs or [])) if with_subject else text

    if llm is None:
        return done(_matching_docs(category, scope))

    candidates = _candidate_docs(scope)
    if not candidates:
        return done([])

    index = _build_index(candidates)
    try:
        result = llm.complete(
            "inbound/select_docs",
            {
                "inquiry": (inquiry or "").strip() or "(no message body)",
                "category": category,
                "inquiry_language": (language or "unknown"),
                "doc_index": index,
            },
            schema=SelectDocsResult,
            tier="flash",
        )
        wanted = {s.strip().lower() for s in (result.slugs or []) if s.strip()}
    except Exception:
        logger.warning("Doc router failed, falling back to category match.", exc_info=True)
        return done(_matching_docs(category, scope))

    selected = [d for d in candidates if d.slug.lower() in wanted]
    if not selected:
        logger.info("Doc router selected nothing; falling back to category match.")
        return done(_matching_docs(category, scope))

    logger.info(
        "Doc router selected %d/%d docs for category=%s: %s",
        len(selected),
        len(candidates),
        category,
        ", ".join(d.slug for d in selected),
    )
    return done(selected)
