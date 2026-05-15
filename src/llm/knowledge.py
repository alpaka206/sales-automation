"""
Knowledge base loader (DB-backed).

Queries the ``knowledge_documents`` table for documents matching a given
inbound/outbound category and scope, then returns a formatted string ready
to embed in an LLM prompt template.

Rules:
- ``categories`` JSON list may include ``"all"`` → matches every category
  except ``spam``.
- A document with an empty or NULL ``categories`` list is treated as ``[all]``.
- ``scope`` filtering: ``"inbound"`` matches docs with scope ``inbound`` or
  ``both``; same logic for ``"outbound"``.  Passing ``scope="both"`` matches
  everything.
- Spam category always returns empty string.
"""

from __future__ import annotations

from functools import lru_cache

from ..db.models import KnowledgeDocument
from ..db.session import SessionLocal


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


@lru_cache(maxsize=64)
def _load_from_db(category: str, scope: str) -> str:
    """Query DB and format matching documents."""
    session = SessionLocal()
    try:
        docs = session.query(KnowledgeDocument).order_by(KnowledgeDocument.title).all()
    finally:
        session.close()

    matched: list[str] = []
    for doc in docs:
        if not _category_matches(doc.categories, category):
            continue
        if not _scope_matches(doc.scope, scope):
            continue
        matched.append(f"### {doc.title}\n{doc.body}")

    if not matched:
        return ""
    return "## Relevant knowledge base documents\n\n" + "\n\n---\n\n".join(matched)


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
