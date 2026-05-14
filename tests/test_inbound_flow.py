"""End-to-end test for the inbound agent with mocked LLM and DB."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
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
from src.db.base import Base
from src.db.models import Contact, Conversation, Message
from src.integrations.hubspot import ContactDTO, EngagementDTO, DealDTO


@pytest.fixture(autouse=True)
def _clear_dedup():
    _processed.clear()
    yield
    _processed.clear()


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session, engine
    session.close()


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
    session, engine = db_session
    llm = _mock_llm()

    with patch("src.agents.inbound.SessionLocal", return_value=session):
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

    contacts = session.query(Contact).all()
    assert len(contacts) == 1
    assert contacts[0].normalized_email == "buyer@acme.co.kr"

    messages = session.query(Message).all()
    assert len(messages) == 1
    assert messages[0].status == "pending_approval"
    assert messages[0].direction == "outbound"
    assert messages[0].subject == "Re: Inquiry"

    conversations = session.query(Conversation).all()
    assert len(conversations) == 1
    assert conversations[0].topic == "purchase_inquiry"


def test_inbound_dedup(db_session) -> None:
    session, engine = db_session
    llm = _mock_llm()

    event = {
        "object_id": "hs-456",
        "occurred_at": "2026-05-14T11:00:00Z",
        "email": "dup@example.com",
        "full_name": "Dup User",
        "last_message": "Hello",
    }

    with patch("src.agents.inbound.SessionLocal", return_value=session):
        agent = InboundAgent(llm=llm, hubspot=None)
        r1 = agent.handle(event)
        r2 = agent.handle(event)

    assert r1 is not None
    assert r2 is None
    assert session.query(Message).count() == 1


def test_inbound_channel_selection() -> None:
    agent = InboundAgent(llm=_mock_llm(), hubspot=None)

    assert agent._pick_channel({"email": "a@b.com"}) == "email"
    assert agent._pick_channel({}) == "none"


def test_inbound_enriched_from_hubspot(db_session) -> None:
    session, engine = db_session
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

    with patch("src.agents.inbound.SessionLocal", return_value=session):
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

    contacts = session.query(Contact).all()
    assert contacts[0].normalized_email == "enriched@acme.co.kr"
