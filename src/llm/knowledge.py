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
  ``both``; same for ``"outbound"``. ``scope="both"`` matches everything.
- Only ``status == "active"`` documents are ever returned.
- Spam category always returns empty string.
"""

from __future__ import annotations

import logging
from functools import lru_cache

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


def _format_docs(docs: list[KnowledgeDocument]) -> str:
    """Render selected documents as a prompt-ready block."""
    parts = [f"### {doc.title}\n{doc.body}" for doc in docs]
    if not parts:
        return ""
    return "## Relevant knowledge base documents\n\n" + "\n\n---\n\n".join(parts)


@lru_cache(maxsize=64)
def _load_from_db(category: str, scope: str) -> str:
    """Query DB and format matching documents (category mode, cached)."""
    session = SessionLocal()
    try:
        docs = session.query(KnowledgeDocument).order_by(KnowledgeDocument.title).all()
    finally:
        session.close()

    matched = [
        doc
        for doc in docs
        if _is_active(doc)
        and _category_matches(doc.categories, category)
        and _scope_matches(doc.scope, scope)
    ]
    return _format_docs(matched)


def reset_cache() -> None:
    """Invalidate the query cache. Used by tests and by the BE on reload."""
    _load_from_db.cache_clear()


def load_relevant_docs(category: str, scope: str = "inbound") -> str:
    """
    Return a formatted string of all knowledge documents matching *category* and *scope*.

    Empty string if no documents match or if category is spam.
    """
    if not category or category.lower() == "spam":
        return ""
    return _load_from_db(category, scope)


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
) -> str:
    """
    Use the LLM to pick the knowledge documents most relevant to *inquiry*.

    Falls back to deterministic category matching when:
      - the category is spam (→ ""),
      - no ``llm`` is provided,
      - there are no candidate documents,
      - the router errors or selects nothing valid.
    """
    if not category or category.lower() == "spam":
        return ""

    if llm is None:
        return load_relevant_docs(category, scope)

    candidates = _candidate_docs(scope)
    if not candidates:
        return ""

    index = _build_index(candidates)
    try:
        result = llm.complete(
            "inbound/select_docs",
            {
                "inquiry": (inquiry or "").strip() or "(no message body)",
                "category": category,
                "doc_index": index,
            },
            schema=SelectDocsResult,
            tier="flash",
        )
        wanted = {s.strip().lower() for s in (result.slugs or []) if s.strip()}
    except Exception:
        logger.warning("Doc router failed, falling back to category match.", exc_info=True)
        return load_relevant_docs(category, scope)

    selected = [d for d in candidates if d.slug.lower() in wanted]
    if not selected:
        logger.info("Doc router selected nothing; falling back to category match.")
        return load_relevant_docs(category, scope)

    logger.info(
        "Doc router selected %d/%d docs for category=%s: %s",
        len(selected),
        len(candidates),
        category,
        ", ".join(d.slug for d in selected),
    )
    return _format_docs(selected)
