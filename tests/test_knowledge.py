"""Tests for the DB-backed knowledge_base loader."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.base import Base
from src.db.models import KnowledgeDocument
from src.llm import knowledge


@pytest.fixture(autouse=True)
def _db_backed_knowledge(monkeypatch):
    """Point the knowledge loader at an in-memory DB and clear its cache."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(knowledge, "SessionLocal", factory)
    knowledge.reset_cache()
    yield factory
    knowledge.reset_cache()


def _insert(factory, **kwargs) -> None:
    """Helper to insert a KnowledgeDocument."""
    defaults = {"scope": "both", "body": "body"}
    defaults.update(kwargs)
    session = factory()
    session.add(KnowledgeDocument(**defaults))
    session.commit()
    session.close()


def test_category_match(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="2026 Pricing",
        slug="pricing",
        categories=["pricing_question", "purchase_inquiry"],
        body="Body text here.",
    )
    out = knowledge.load_relevant_docs("pricing_question")
    assert "2026 Pricing" in out
    assert "Body text here." in out


def test_no_match_returns_empty(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="Pricing",
        slug="pricing",
        categories=["pricing_question"],
    )
    assert knowledge.load_relevant_docs("recruiting") == ""


def test_all_keyword_matches_every_category(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="About Us",
        slug="about",
        categories=["all"],
        body="We are a company.",
    )
    for cat in ("purchase_inquiry", "partnership", "support", "recruiting", "other"):
        knowledge.reset_cache()
        out = knowledge.load_relevant_docs(cat)
        assert "About Us" in out, f"missing for category={cat}"


def test_empty_categories_defaults_to_all(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="General Info",
        slug="general",
        categories=[],
        body="Applies to everything.",
    )
    assert "General Info" in knowledge.load_relevant_docs("partnership")
    knowledge.reset_cache()
    assert "General Info" in knowledge.load_relevant_docs("support")


def test_null_categories_defaults_to_all(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="General Info",
        slug="general",
        categories=None,
        body="Applies to everything.",
    )
    assert "General Info" in knowledge.load_relevant_docs("partnership")


def test_spam_category_returns_empty(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="General",
        slug="general",
        categories=["all"],
        body="Applies to everything.",
    )
    assert knowledge.load_relevant_docs("spam") == ""


def test_empty_db_returns_empty(_db_backed_knowledge) -> None:
    assert knowledge.load_relevant_docs("purchase_inquiry") == ""


def test_multiple_matches_are_separated(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="Pricing A",
        slug="a-pricing",
        categories=["pricing_question"],
        body="A body.",
    )
    _insert(
        _db_backed_knowledge,
        title="Plans B",
        slug="b-plans",
        categories=["pricing_question"],
        body="B body.",
    )
    out = knowledge.load_relevant_docs("pricing_question")
    assert "Pricing A" in out
    assert "Plans B" in out
    assert "---" in out


def test_scope_inbound_filters_outbound_docs(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="Inbound Only",
        slug="inbound-only",
        categories=["all"],
        scope="inbound",
    )
    _insert(
        _db_backed_knowledge,
        title="Outbound Only",
        slug="outbound-only",
        categories=["all"],
        scope="outbound",
    )
    out = knowledge.load_relevant_docs("purchase_inquiry", scope="inbound")
    assert "Inbound Only" in out
    assert "Outbound Only" not in out


def test_scope_both_matches_all(_db_backed_knowledge) -> None:
    _insert(_db_backed_knowledge, title="Both", slug="both", categories=["all"], scope="both")
    _insert(_db_backed_knowledge, title="In", slug="in", categories=["all"], scope="inbound")
    _insert(_db_backed_knowledge, title="Out", slug="out", categories=["all"], scope="outbound")
    out = knowledge.load_relevant_docs("purchase_inquiry", scope="both")
    assert "Both" in out
    assert "In" in out
    assert "Out" in out


def test_scope_default_is_inbound(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="Outbound Doc",
        slug="outbound",
        categories=["all"],
        scope="outbound",
    )
    assert knowledge.load_relevant_docs("purchase_inquiry") == ""
