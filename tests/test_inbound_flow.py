"""End-to-end test for the inbound agent with mocked LLM and DB."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from src.agents.inbound import (
    InboundAgent,
    ClassifyResult,
    ScoreAdjustResult,
    DraftResult,
    _base_score,
    _normalize_email,
    _processed,
)
from src.db.models import Contact, Conversation, Message
from src.integrations.hubspot import ContactDTO, EngagementDTO, DealDTO
from src.llm import knowledge


@pytest.fixture(autouse=True)
def _clear_dedup():
    _processed.clear()
    yield
    _processed.clear()


@pytest.fixture(autouse=True)
def _isolated_knowledge_base(tmp_path, monkeypatch):
    """Keep tests independent from the repo's knowledge_base/ contents."""
    empty = tmp_path / "kb_empty"
    empty.mkdir()
    monkeypatch.setattr(knowledge, "KNOWLEDGE_DIR", empty)
    knowledge.reset_cache()
    yield empty
    knowledge.reset_cache()


def _mock_llm():
    llm = MagicMock()

    def side_effect(prompt_name, variables=None, schema=None, **kw):
        if "classify" in prompt_name:
            return ClassifyResult(category="purchase_inquiry", reasoning="Wants to buy")
        if "score_adjust" in prompt_name:
            return ScoreAdjustResult(adjustment=10, reasoning="High urgency")
        if "draft_reply" in prompt_name:
            return DraftResult(
                subject="Re: Inquiry",
                body="Thank you for your interest.",
                language="ko",
                tone_notes="formal",
            )
        return "ok"

    llm.complete = MagicMock(side_effect=side_effect)
    return llm


def test_normalize_email() -> None:
    assert _normalize_email("Foo+tag@Bar.COM") == "foo@bar.com"
    assert _normalize_email("user@example.com") == "user@example.com"


def test_base_score_enterprise() -> None:
    score = _base_score("ceo@company.co.kr", "korea")
    assert score == 80  # 50 + 15 (enterprise) + 15 (country)


def test_base_score_personal() -> None:
    score = _base_score("person@gmail.com", "us")
    assert score == 40  # 50 - 10 (personal)


def test_inbound_handle_creates_db_rows(db_session) -> None:
    llm = _mock_llm()

    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=llm, hubspot=None)
        result = agent.handle({
            "object_id": "hs-123",
            "occurred_at": "2026-05-14T10:00:00Z",
            "email": "buyer@acme.co.kr",
            "full_name": "Kim Buyer",
            "company": "Acme Corp",
            "country": "korea",
            "last_message": "We want to purchase your product.",
        })

    assert result is not None
    assert result["category"] == "purchase_inquiry"
    assert result["channel"] == "email"
    assert result["score"] > 0

    contacts = db_session.query(Contact).all()
    assert len(contacts) == 1
    assert contacts[0].normalized_email == "buyer@acme.co.kr"

    messages = db_session.query(Message).all()
    assert len(messages) == 1
    assert messages[0].status == "pending_approval"
    assert messages[0].direction == "outbound"
    assert messages[0].subject == "Re: Inquiry"

    conversations = db_session.query(Conversation).all()
    assert len(conversations) == 1
    assert conversations[0].topic == "purchase_inquiry"


def test_inbound_dedup(db_session) -> None:
    llm = _mock_llm()

    event = {
        "object_id": "hs-456",
        "occurred_at": "2026-05-14T11:00:00Z",
        "email": "dup@example.com",
        "full_name": "Dup User",
        "last_message": "Hello",
    }

    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=llm, hubspot=None)
        r1 = agent.handle(event)
        r2 = agent.handle(event)

    assert r1 is not None
    assert r2 is None
    assert db_session.query(Message).count() == 1


def test_inbound_channel_selection() -> None:
    agent = InboundAgent(llm=_mock_llm(), hubspot=None)

    assert agent._pick_channel({"email": "a@b.com"}) == "email"
    assert agent._pick_channel({}) == "none"


def test_inbound_enriched_from_hubspot(db_session) -> None:
    llm = _mock_llm()

    mock_hs = MagicMock()
    mock_hs.get_contact_sync.return_value = ContactDTO(
        id="hs-999",
        email="enriched@acme.co.kr",
        firstname="Kim",
        lastname="Enriched",
        company="Acme Enriched",
        phone="+8210-1234",
        country="korea",
        lifecyclestage="opportunity",
    )
    mock_hs.get_recent_emails_sync.return_value = [
        EngagementDTO(id="e1", type="email", subject="Previous email", body="We discussed pricing."),
    ]
    mock_hs.get_associated_deals_sync.return_value = [
        DealDTO(id="d1", name="Acme Deal", stage="negotiation", amount="50000"),
    ]

    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=llm, hubspot=mock_hs)
        result = agent.handle({
            "object_id": "hs-999",
            "occurred_at": "2026-05-14T12:00:00Z",
            "email": "orig@acme.co.kr",
            "full_name": "Orig Name",
            "last_message": "We want to proceed.",
        })

    assert result is not None

    classify_call = llm.complete.call_args_list[0]
    classify_vars = classify_call[0][1]
    assert classify_vars["contact_name"] == "Kim Enriched"
    assert "Previous email" in classify_vars["enrichment_context"]
    assert "Acme Deal" in classify_vars["enrichment_context"]

    contacts = db_session.query(Contact).all()
    assert contacts[0].normalized_email == "enriched@acme.co.kr"


def test_inbound_passes_knowledge_docs_to_draft(db_session, tmp_path, monkeypatch) -> None:
    """When classification matches a knowledge_base doc, its body must reach the draft prompt."""
    kb = tmp_path / "kb_with_doc"
    kb.mkdir()
    (kb / "pricing.md").write_text(
        "---\ntitle: Plans\ncategories: [purchase_inquiry]\n---\n\nStarter plan starts at 99k KRW.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(knowledge, "KNOWLEDGE_DIR", kb)
    knowledge.reset_cache()

    llm = _mock_llm()
    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=llm, hubspot=None)
        agent.handle({
            "object_id": "hs-kb-1",
            "occurred_at": "2026-05-14T13:00:00Z",
            "email": "kb@acme.co.kr",
            "full_name": "KB Tester",
            "last_message": "What plans do you offer?",
        })

    draft_call = next(
        c for c in llm.complete.call_args_list if "draft_reply" in c[0][0]
    )
    draft_vars = draft_call[0][1]
    assert "knowledge_docs" in draft_vars
    assert "Plans" in draft_vars["knowledge_docs"]
    assert "Starter plan starts at 99k KRW." in draft_vars["knowledge_docs"]


def test_inbound_omits_knowledge_for_spam(db_session, tmp_path, monkeypatch) -> None:
    """Spam classification must not pull any knowledge docs into the draft prompt."""
    kb = tmp_path / "kb_spam"
    kb.mkdir()
    (kb / "general.md").write_text(
        "---\ncategories: [all]\n---\n\nAlways-on company info.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(knowledge, "KNOWLEDGE_DIR", kb)
    knowledge.reset_cache()

    llm = MagicMock()

    def side_effect(prompt_name, variables=None, schema=None, **kw):
        if "classify" in prompt_name:
            return ClassifyResult(category="spam", reasoning="Junk")
        if "score_adjust" in prompt_name:
            return ScoreAdjustResult(adjustment=-50, reasoning="spam")
        if "draft_reply" in prompt_name:
            return DraftResult(subject="", body="", language="en")
        return "ok"

    llm.complete = MagicMock(side_effect=side_effect)

    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=llm, hubspot=None)
        agent.handle({
            "object_id": "hs-spam-1",
            "occurred_at": "2026-05-14T14:00:00Z",
            "email": "spam@example.com",
            "full_name": "Spammer",
            "last_message": "BUY VIAGRA CHEAP",
        })

    draft_call = next(
        c for c in llm.complete.call_args_list if "draft_reply" in c[0][0]
    )
    assert draft_call[0][1]["knowledge_docs"] == ""
