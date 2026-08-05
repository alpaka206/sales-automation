"""Tests for the DB-backed knowledge_base loader."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.base import Base
from src.db.models import KnowledgeDocument
from src.llm import knowledge
from src.llm.knowledge import SelectDocsResult


class _FakeLLM:
    """Stub LLM whose router call returns a fixed set of slugs."""

    def __init__(self, slugs: list[str]) -> None:
        self.slugs = slugs
        self.calls: list[dict] = []

    def complete(self, prompt_name, variables=None, schema=None, tier="flash", **kwargs):
        self.calls.append({"prompt": prompt_name, "variables": variables, "tier": tier})
        return SelectDocsResult(slugs=self.slugs, reasoning="stub")


class _BoomLLM:
    """Stub LLM whose router call raises, to exercise the fallback path."""

    def complete(self, *args, **kwargs):
        raise RuntimeError("router down")


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


def test_spam_still_gets_documents(_db_backed_knowledge) -> None:
    """It used to short-circuit to "". The operator's rule is that a 영업·홍보 목적의
    문의에도 회신은 나가고, 그 회신이 볼 것이 소개 문서입니다 — so taking the documents
    away left exactly that one reply written from nothing."""
    _insert(
        _db_backed_knowledge,
        title="General",
        slug="general",
        categories=["all"],
        body="Applies to everything.",
    )
    assert "General" in knowledge.load_relevant_docs("spam")


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


def test_scope_inbound_includes_shared_docs(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="Inbound Only",
        slug="inbound-only",
        categories=["all"],
        scope="inbound",
    )
    _insert(_db_backed_knowledge, title="Shared", slug="shared", categories=["all"], scope="both")
    out = knowledge.load_relevant_docs("purchase_inquiry", scope="inbound")
    assert "Inbound Only" in out
    assert "Shared" in out


def test_scope_both_matches_all(_db_backed_knowledge) -> None:
    _insert(_db_backed_knowledge, title="Both", slug="both", categories=["all"], scope="both")
    _insert(_db_backed_knowledge, title="In", slug="in", categories=["all"], scope="inbound")
    out = knowledge.load_relevant_docs("purchase_inquiry", scope="both")
    assert "Both" in out
    assert "In" in out


def test_archived_docs_excluded_from_category_match(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="Archived Pricing",
        slug="archived",
        categories=["pricing_question"],
        body="old body",
        status="archived",
    )
    assert knowledge.load_relevant_docs("pricing_question") == ""


# ---- LLM document router ----


def test_router_selects_only_returned_slugs(_db_backed_knowledge) -> None:
    _insert(_db_backed_knowledge, title="Pricing", slug="pricing",
            categories=["pricing_question"], body="pricing body", summary="prices")
    _insert(_db_backed_knowledge, title="Refund", slug="refund",
            categories=["support"], body="refund body", summary="refunds")
    llm = _FakeLLM(slugs=["refund"])
    out = knowledge.select_relevant_docs("환불 되나요?", "support", llm=llm)
    assert "Refund" in out
    assert "Pricing" not in out
    # router runs on the cheap flash tier
    assert llm.calls and llm.calls[0]["tier"] == "flash"


def test_router_none_llm_falls_back_to_category(_db_backed_knowledge) -> None:
    _insert(_db_backed_knowledge, title="Pricing", slug="pricing",
            categories=["pricing_question"], body="pricing body")
    out = knowledge.select_relevant_docs("price?", "pricing_question", llm=None)
    assert "Pricing" in out


def test_router_empty_selection_falls_back_to_category(_db_backed_knowledge) -> None:
    _insert(_db_backed_knowledge, title="Pricing", slug="pricing",
            categories=["pricing_question"], body="pricing body")
    llm = _FakeLLM(slugs=[])  # selects nothing
    out = knowledge.select_relevant_docs("price?", "pricing_question", llm=llm)
    assert "Pricing" in out  # fell back to category match


def test_router_error_falls_back_to_category(_db_backed_knowledge) -> None:
    _insert(_db_backed_knowledge, title="Pricing", slug="pricing",
            categories=["pricing_question"], body="pricing body")
    out = knowledge.select_relevant_docs("price?", "pricing_question", llm=_BoomLLM())
    assert "Pricing" in out


def test_the_router_picks_documents_for_spam_too(_db_backed_knowledge) -> None:
    """The router decides; spam is no longer refused at the door. Selecting nothing is
    still its own call to make — the prompt says so — but it has to be a judgement about
    this inquiry, not a rule that fires before the model sees it."""
    _insert(_db_backed_knowledge, title="X", slug="x", categories=["all"], body="b")
    assert "X" in knowledge.select_relevant_docs("buy viagra", "spam", llm=_FakeLLM(["x"]))


def test_router_ignores_archived_candidates(_db_backed_knowledge) -> None:
    _insert(_db_backed_knowledge, title="Live", slug="live",
            categories=["pricing_question"], body="live body", status="active")
    _insert(_db_backed_knowledge, title="Old", slug="old",
            categories=["pricing_question"], body="old body", status="archived")
    # Even if the model names the archived slug, it isn't a candidate.
    llm = _FakeLLM(slugs=["old", "live"])
    out = knowledge.select_relevant_docs("price?", "pricing_question", llm=llm)
    assert "Live" in out
    assert "Old" not in out
