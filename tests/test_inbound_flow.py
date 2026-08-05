"""End-to-end test for the inbound agent with mocked LLM and DB."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from unittest.mock import patch, MagicMock

from src.agents.inbound import (
    InboundAgent,
    ClassifyResult,
    ScoreAdjustResult,
    DraftResult,
    _SummaryResult,
    _base_score,
    _normalize_email,
    _processed,
)
from src.common.config import settings
from src.db.models import Contact, Conversation, InboundJob, Message
from src.db.models import KnowledgeDocument
from src.integrations.hubspot import ContactDTO, EngagementDTO, DealDTO
from src.llm import knowledge


@pytest.fixture(autouse=True)
def _clear_dedup():
    _processed.clear()
    yield
    _processed.clear()


@pytest.fixture(autouse=True)
def _no_auto_ack(monkeypatch):
    """Disable the immediate auto-ack here so message-count assertions stay exact.

    The auto-ack is covered on its own in test_inbound_auto_ack.py.
    """
    monkeypatch.setattr(settings, "INBOUND_AUTO_ACK_ENABLED", False)


@pytest.fixture(autouse=True)
def _isolated_knowledge_db(db_session, monkeypatch):
    """Point knowledge loader at the test DB session so it starts empty."""
    factory = lambda: db_session  # noqa: E731
    monkeypatch.setattr(knowledge, "SessionLocal", factory)
    knowledge.reset_cache()
    yield db_session
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
        if "summarize_thread" in prompt_name:
            return _SummaryResult(summary="요약입니다.", customer_requests="- 요청사항")
        if "detect_language" in prompt_name:
            return "en"
        if "translate_ko" in prompt_name:
            return "관심 가져주셔서 감사합니다."
        if "translate_to" in prompt_name:
            return "translated text"
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
        result = agent.handle(
            {
                "object_id": "hs-123",
                "occurred_at": "2026-05-14T10:00:00Z",
                "email": "buyer@acme.co.kr",
                "full_name": "Kim Buyer",
                "company": "Acme Corp",
                "country": "korea",
                "last_message": "We want to purchase your product.",
                "subject": "Bulk dubbing quote",
            }
        )

    assert result is not None
    assert result["category"] == "purchase_inquiry"
    assert result["channel"] == "email"
    assert result["score"] > 0

    # 유형은 저장됩니다(0049). 목록이 채널 자리에 이것을 보여주고 — 채널은 전 행이 "email"
    # 이라 아무것도 구분하지 못했습니다 — "검토 필요" 문구가 하던 일도 이쪽이 합니다.
    conversation = db_session.query(Conversation).one()
    assert conversation.inquiry_category == "purchase_inquiry"

    contacts = db_session.query(Contact).all()
    assert len(contacts) == 1
    assert contacts[0].normalized_email == "buyer@acme.co.kr"

    messages = db_session.query(Message).all()
    assert len(messages) == 2
    inbound_msg = [m for m in messages if m.direction == "inbound"]
    reply_msg = [m for m in messages if m.direction == "outgoing"]
    assert len(inbound_msg) == 1
    assert inbound_msg[0].status == "received"
    assert len(reply_msg) == 1
    assert reply_msg[0].status == "pending_approval"
    # Subject is built in code as "RE: <customer subject or localized generic>",
    # never the raw model subject.
    assert reply_msg[0].subject == "RE: Bulk dubbing quote"
    # Draft is always Korean; the language to SEND in is the detected inquiry language.
    assert reply_msg[0].language == "ko"
    assert reply_msg[0].target_language == "en"

    conversations = db_session.query(Conversation).all()
    assert len(conversations) == 1
    # The AI category is no longer persisted — it routes knowledge docs and adjusts the
    # score inside this run and is then discarded. The column now holds the customer's
    # own subject line instead (migration 0041).
    assert conversations[0].inquiry_subject == "Bulk dubbing quote"


def test_personal_domain_not_stored(db_session) -> None:
    """Personal/free-email senders (gmail) must not get a company domain — otherwise
    unrelated customers would be grouped and their history cross-exposed."""
    llm = _mock_llm()
    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=llm, hubspot=None)
        agent.handle(
            {
                "object_id": "hs-personal",
                "occurred_at": "2026-05-14T10:30:00Z",
                "email": "someone@gmail.com",
                "full_name": "Personal User",
                "last_message": "Hi, a question.",
            }
        )
    c = db_session.query(Contact).filter_by(normalized_email="someone@gmail.com").first()
    assert c is not None
    assert c.domain is None


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
    assert r2 is not None
    assert r2["status"] == "skipped_existing_pending"
    assert db_session.query(Message).filter_by(direction="outgoing").count() == 1


def test_durable_retry_resumes_linked_placeholder(db_session, db_session_factory) -> None:
    """A lease retry finishes the same draft instead of leaving it spinning forever."""
    now = datetime.now(timezone.utc)
    job = InboundJob(
        event_key="hubspot:ticket:T-resume:created",
        source="webhook",
        payload={"ticket_id": "T-resume"},
        status="processing",
        attempts=1,
        available_at=now,
        locked_at=now,
    )
    db_session.add(job)
    db_session.commit()

    info = {
        "object_id": "hs-resume",
        "ticket_id": "T-resume",
        "ticket_stage": settings.HUBSPOT_TICKET_STAGE_NEW,
        "email": "resume@example.com",
        "full_name": "Resume User",
        "company": "Resume Co",
        "country": "us",
        "last_message": "Please tell me about the service.",
    }
    with patch("src.agents.inbound.SessionLocal", db_session_factory):
        agent = InboundAgent(llm=_mock_llm(), hubspot=None)
        message_id, _, _ = agent._persist_placeholder(
            info, "email", "en", inbound_job_id=job.id
        )

        db_session.expire_all()
        assert db_session.get(InboundJob, job.id).payload["draft_message_id"] == message_id

        result = agent.handle(
            {
                **info,
                "_inbound_job_id": job.id,
                "_draft_message_id": message_id,
            }
        )

    assert result["message_id"] == message_id
    assert db_session.query(Message).filter_by(direction="inbound").count() == 1
    detailed = (
        db_session.query(Message)
        .filter(Message.direction == "outgoing", Message.prompt_variant.is_(None))
        .all()
    )
    assert len(detailed) == 1
    assert detailed[0].status == "pending_approval"


def test_later_contact_reply_marks_latest_sent_message_answered(db_session) -> None:
    llm = _mock_llm()
    first_event = {
        "object_id": "hs-returning",
        "occurred_at": "2026-05-14T11:00:00Z",
        "email": "returning@example.com",
        "full_name": "Returning Buyer",
        "last_message": "Please send pricing.",
    }
    second_event = {
        **first_event,
        "occurred_at": "2026-05-15T11:00:00Z",
        "last_message": "Thanks. Can we meet tomorrow?",
    }

    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=llm, hubspot=None)
        first = agent.handle(first_event)
        sent = db_session.get(Message, first["message_id"])
        sent.status = "sent"
        sent.sent_at = sent.created_at
        db_session.commit()

        second = agent.handle(second_event)

    assert second is not None
    assert db_session.get(Message, first["message_id"]).replied is True
    assert db_session.query(Message).filter_by(direction="outgoing").count() == 2


def test_test_sent_reply_is_marked_answered_in_safe_mode(db_session) -> None:
    """Pre-launch safe mode stores "test_sent", not "sent" — replies must still count.

    Matching only status=="sent" left Message.replied permanently False before
    go-live, zeroing the reply-rate report and /messages?status=replied.
    """
    llm = _mock_llm()
    first_event = {
        "object_id": "hs-safe-mode-reply",
        "occurred_at": "2026-05-14T11:00:00Z",
        "email": "safemode@example.com",
        "full_name": "Safe Mode Buyer",
        "last_message": "Please send pricing.",
    }
    second_event = {
        **first_event,
        "occurred_at": "2026-05-15T11:00:00Z",
        "last_message": "Thanks. Can we meet tomorrow?",
    }

    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=llm, hubspot=None)
        first = agent.handle(first_event)
        sent = db_session.get(Message, first["message_id"])
        sent.status = "test_sent"  # what safe mode actually writes
        sent.sent_at = sent.created_at
        db_session.commit()

        agent.handle(second_event)

    assert db_session.get(Message, first["message_id"]).replied is True


def test_inbound_channel_selection() -> None:
    agent = InboundAgent(llm=_mock_llm(), hubspot=None)

    assert agent._pick_channel({"email": "a@b.com", "phone": "+821012345678"}) == "email"
    assert agent._pick_channel({"email": "a@b.com"}) == "email"
    assert agent._pick_channel({}) == "none"


def test_inbound_skips_ticket_outside_new_stage(monkeypatch) -> None:
    from src.agents.inbound import _processed
    from src.common.config import settings

    monkeypatch.setattr(settings, "HUBSPOT_TICKET_STAGE_NEW", "1")
    agent = InboundAgent.__new__(InboundAgent)
    agent.llm = _mock_llm()
    agent.hubspot = None
    agent._fetch_contact = MagicMock(
        return_value={
            "object_id": "hs-stage",
            "ticket_id": "ticket-1",
            "ticket_stage": "2",
            "last_message": "Question",
        }
    )
    _processed.discard("hs-stage:stage-test")

    result = agent.handle({"object_id": "hs-stage", "occurred_at": "stage-test"})

    assert result["status"] == "skipped_not_new"


def test_inbound_enriched_from_hubspot(db_session, monkeypatch) -> None:
    # This test asserts on the *classify* call (call_args_list[0]) and on
    # email/deal enrichment — not domain enrichment. Disable domain enrichment so
    # it doesn't fire an earlier LLM call (which would shift call_args_list).
    from src.common.config import settings

    monkeypatch.setattr(settings, "INBOUND_DOMAIN_ENRICHMENT_ENABLED", False)
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
        EngagementDTO(
            id="e1", type="email", subject="Previous email", body="We discussed pricing."
        ),
    ]
    mock_hs.get_associated_deals_sync.return_value = [
        DealDTO(id="d1", name="Acme Deal", stage="negotiation", amount="50000"),
    ]

    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=llm, hubspot=mock_hs)
        result = agent.handle(
            {
                "object_id": "hs-999",
                "occurred_at": "2026-05-14T12:00:00Z",
                "email": "orig@acme.co.kr",
                "full_name": "Orig Name",
                "last_message": "We want to proceed.",
            }
        )

    assert result is not None

    # Find the classify call by name — language detection now runs before it, so it's
    # no longer guaranteed to be call index 0.
    classify_call = next(c for c in llm.complete.call_args_list if "classify" in c[0][0])
    classify_vars = classify_call[0][1]
    assert classify_vars["contact_name"] == "Kim Enriched"
    assert "Previous email" in classify_vars["enrichment_context"]
    assert "Acme Deal" in classify_vars["enrichment_context"]

    contacts = db_session.query(Contact).all()
    assert contacts[0].normalized_email == "enriched@acme.co.kr"


def test_inbound_passes_knowledge_docs_to_draft(db_session) -> None:
    """When classification matches a knowledge_base doc, its body must reach the draft prompt."""
    knowledge.reset_cache()
    doc = KnowledgeDocument(
        title="Plans",
        slug="plans",
        categories=["purchase_inquiry"],
        scope="both",
        body="Starter plan starts at 99k KRW.",
    )
    db_session.add(doc)
    db_session.commit()

    llm = _mock_llm()
    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=llm, hubspot=None)
        agent.handle(
            {
                "object_id": "hs-kb-1",
                "occurred_at": "2026-05-14T13:00:00Z",
                "email": "kb@acme.co.kr",
                "full_name": "KB Tester",
                "last_message": "What plans do you offer?",
            }
        )

    draft_call = next(c for c in llm.complete.call_args_list if "draft_reply" in c[0][0])
    draft_vars = draft_call[0][1]
    assert "knowledge_docs" in draft_vars
    assert "Plans" in draft_vars["knowledge_docs"]
    assert "Starter plan starts at 99k KRW." in draft_vars["knowledge_docs"]


def test_a_spam_classification_still_gets_documents(db_session) -> None:
    """It used to be refused at the door. 영업·홍보 목적의 문의에도 회신은 나가고,
    그 회신이 볼 것이 소개 문서입니다 — so the one reply written from no source at all
    was exactly the one this rule produced. What to send is the operator's call on the
    draft, not something decided by withholding the documents."""
    knowledge.reset_cache()
    doc = KnowledgeDocument(
        title="General",
        slug="general",
        categories=["all"],
        scope="both",
        body="Always-on company info.",
    )
    db_session.add(doc)
    db_session.commit()

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
        agent.handle(
            {
                "object_id": "hs-spam-1",
                "occurred_at": "2026-05-14T14:00:00Z",
                "email": "spam@example.com",
                "full_name": "Spammer",
                "last_message": "BUY VIAGRA CHEAP",
            }
        )

    draft_call = next(c for c in llm.complete.call_args_list if "draft_reply" in c[0][0])
    assert "Always-on company info." in draft_call[0][1]["knowledge_docs"]
