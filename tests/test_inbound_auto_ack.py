"""The immediate auto-acknowledgement: first inbound only, in the inquiry's
language, without approval, recorded in the thread, and never duplicated."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from src.agents.inbound import (
    ClassifyResult,
    DraftResult,
    InboundAgent,
    ScoreAdjustResult,
    _SummaryResult,
    _processed,
)
from src.common.config import settings
from src.db.models import Approval, Contact, Conversation, Message


@pytest.fixture(autouse=True)
def _clear_dedup():
    _processed.clear()
    yield
    _processed.clear()


def _mock_llm():
    llm = MagicMock()

    def side_effect(prompt_name, variables=None, schema=None, **kw):
        if "classify" in prompt_name:
            return ClassifyResult(category="pricing_question", reasoning="x")
        if "score_adjust" in prompt_name:
            return ScoreAdjustResult(adjustment=0, reasoning="x")
        if "draft_reply" in prompt_name:
            return DraftResult(subject="s", body="본문입니다.", language="ko")
        if "summarize_thread" in prompt_name:
            return _SummaryResult(summary="요약", customer_requests="")
        if "detect_language" in prompt_name:
            return "en"
        if "translate_to" in prompt_name:
            return "We received your message and will reply within 24 hours."
        if "translate_ko" in prompt_name:
            return "한국어 번역"
        return "ok"

    llm.complete = MagicMock(side_effect=side_effect)
    return llm


_EVENT = {
    "object_id": "hs-ack-1",
    "occurred_at": "2026-06-18T10:00:00Z",
    "email": "buyer@acme.com",
    "full_name": "Buyer",
    "last_message": "Hello, I have a question about dubbing.",
}


@patch("src.agents.inbound.select_relevant_docs", return_value="")
@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_auto_ack_sent_on_first_inbound(
    mock_send, _docs, db_session, db_session_factory, monkeypatch
):
    monkeypatch.setattr(settings, "INBOUND_AUTO_ACK_ENABLED", True)
    with (
        patch("src.agents.inbound.SessionLocal", db_session_factory),
        patch("src.agents.send_worker.SessionLocal", db_session_factory),
    ):
        agent = InboundAgent(llm=_mock_llm(), hubspot=None)
        agent.handle(dict(_EVENT))

    mock_send.assert_awaited()  # auto-ack dispatched inline, no approval
    acks = db_session.query(Message).filter_by(prompt_variant="auto_ack").all()
    assert len(acks) == 1
    ack = acks[0]
    assert ack.direction == "outgoing"
    assert ack.status == "sent"
    # Mandatory: goes out in the inquiry's language (translated in code).
    assert ack.target_language == "en"
    assert ack.language == "en"
    assert ack.body == "We received your message and will reply within 24 hours."
    detailed = (
        db_session.query(Message)
        .filter(Message.direction == "outgoing", Message.prompt_variant.is_(None))
        .one()
    )
    assert detailed.status == "pending_approval"


@patch("src.agents.inbound.select_relevant_docs", return_value="")
@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_auto_ack_not_duplicated(
    mock_send, _docs, db_session, db_session_factory, monkeypatch
):
    monkeypatch.setattr(settings, "INBOUND_AUTO_ACK_ENABLED", True)
    with (
        patch("src.agents.inbound.SessionLocal", db_session_factory),
        patch("src.agents.send_worker.SessionLocal", db_session_factory),
    ):
        agent = InboundAgent(llm=_mock_llm(), hubspot=None)
        agent.handle(dict(_EVENT))
        # A second event for the same contact must not produce a second auto-ack.
        agent.handle(dict(_EVENT, occurred_at="2026-06-18T12:00:00Z"))

    acks = db_session.query(Message).filter_by(prompt_variant="auto_ack").count()
    assert acks == 1


def test_database_rejects_duplicate_auto_ack(db_session):
    contact = Contact(normalized_email="ack-constraint@example.com", full_name="Ack")
    db_session.add(contact)
    db_session.flush()
    conversation = Conversation(contact_id=contact.id)
    db_session.add(conversation)
    db_session.flush()
    db_session.add(
        Message(
            conversation_id=conversation.id,
            direction="outgoing",
            channel="email",
            body="first",
            prompt_variant="auto_ack",
        )
    )
    db_session.commit()
    db_session.add(
        Message(
            conversation_id=conversation.id,
            direction="outgoing",
            channel="email",
            body="second",
            prompt_variant="auto_ack",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@patch("src.agents.inbound.select_relevant_docs", return_value="")
@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_auto_ack_disabled(mock_send, _docs, db_session, monkeypatch):
    monkeypatch.setattr(settings, "INBOUND_AUTO_ACK_ENABLED", False)
    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=_mock_llm(), hubspot=None)
        agent.handle(dict(_EVENT))

    assert db_session.query(Message).filter_by(prompt_variant="auto_ack").count() == 0
    mock_send.assert_not_awaited()


@patch("src.agents.inbound.select_relevant_docs", return_value="")
def test_auto_ack_is_queued_before_sheet_write(_docs, db_session_factory, monkeypatch):
    monkeypatch.setattr(settings, "INBOUND_AUTO_ACK_ENABLED", True)
    order: list[str] = []
    with (
        patch("src.agents.inbound.SessionLocal", db_session_factory),
        patch.object(InboundAgent, "_maybe_send_auto_ack", side_effect=lambda *a: order.append("ack")),
        patch.object(
            InboundAgent,
            "_mirror_new_inbound_to_sheet",
            side_effect=lambda *a: order.append("sheet"),
        ),
    ):
        InboundAgent(llm=_mock_llm(), hubspot=None).handle(dict(_EVENT))

    assert order[:2] == ["ack", "sheet"]


@patch("src.agents.inbound.notify_approval_once")
@patch("src.agents.inbound.select_relevant_docs", return_value="")
def test_detailed_reply_is_never_auto_approved(
    _docs, mock_notify, db_session, db_session_factory, monkeypatch
):
    """A detailed reply always waits for a human — structurally, not by configuration.

    This used to be a score-vs-AUTO_SEND_THRESHOLD comparison that could set
    "approved" on its own, held shut only by the threshold being above 1.0. The
    operator's rule is that nothing but the receipt acknowledgement ever sends
    unattended, so the branch is gone and there is no setting left to reopen it.
    """
    monkeypatch.setattr(settings, "INBOUND_AUTO_ACK_ENABLED", False)
    monkeypatch.setattr(settings, "SEND_WORKER_ENABLED", True)
    with patch("src.agents.inbound.SessionLocal", db_session_factory):
        InboundAgent(llm=_mock_llm(), hubspot=None).handle(dict(_EVENT))

    reply = (
        db_session.query(Message)
        .filter(Message.direction == "outgoing", Message.prompt_variant.is_(None))
        .one()
    )
    assert reply.status == "pending_approval"
    assert reply.approved_by is None
    assert db_session.query(Approval).filter_by(message_id=reply.id).count() == 0
    # And an operator is told there is something to review.
    mock_notify.assert_called_once()


def test_an_english_inquiry_uses_the_english_template_instead_of_translating_the_korean(
    monkeypatch,
):
    """The acknowledgement goes out with no human in front of it. Machine-translating the
    Korean on every send is a Gemini call the customer waits through, and a sentence that
    comes out slightly different every time — for the one mail that should be identical
    every time. 0053 gives English its own row; other languages still translate."""
    from unittest.mock import patch

    monkeypatch.setattr(settings, "INBOUND_AUTO_ACK_ENABLED", True)
    bodies = {"auto_ack": "안녕하세요 {name}님", "auto_ack_en": "Hi {name}, thanks."}
    with (
        patch("src.db.email_templates.get_email_template", side_effect=lambda k, **kw: bodies.get(k)),
        patch("src.llm.translate.translate_to") as translate,
    ):
        from src.agents.inbound import InboundAgent

        agent = InboundAgent(llm=MagicMock(), hubspot=None)
        with patch.object(agent, "_persist_auto_ack", return_value=None) as persist:
            agent._maybe_send_auto_ack(
                {"full_name": "Jane", "subject": "Question"}, conv_id=1, inquiry_lang="en"
            )

    translate.assert_not_called()
    assert "Hi Jane, thanks." in persist.call_args[0][3]
