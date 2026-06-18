"""The immediate auto-acknowledgement: first inbound only, in the inquiry's
language, without approval, recorded in the thread, and never duplicated."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.inbound import (
    ClassifyResult,
    DraftResult,
    InboundAgent,
    ScoreAdjustResult,
    _SummaryResult,
    _processed,
)
from src.common.config import settings
from src.db.models import Message


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
def test_auto_ack_sent_on_first_inbound(mock_send, _docs, db_session, monkeypatch):
    monkeypatch.setattr(settings, "INBOUND_AUTO_ACK_ENABLED", True)
    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=_mock_llm(), hubspot=None)
        agent.handle(dict(_EVENT))

    mock_send.assert_awaited()  # auto-ack dispatched inline, no approval
    acks = db_session.query(Message).filter_by(prompt_variant="auto_ack").all()
    assert len(acks) == 1
    ack = acks[0]
    assert ack.direction == "outbound"
    assert ack.status == "sent"
    # Mandatory: goes out in the inquiry's language (translated in code).
    assert ack.target_language == "en"
    assert ack.language == "en"
    assert ack.body == "We received your message and will reply within 24 hours."


@patch("src.agents.inbound.select_relevant_docs", return_value="")
@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_auto_ack_not_duplicated(mock_send, _docs, db_session, monkeypatch):
    monkeypatch.setattr(settings, "INBOUND_AUTO_ACK_ENABLED", True)
    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=_mock_llm(), hubspot=None)
        agent.handle(dict(_EVENT))
        # A second event for the same contact must not produce a second auto-ack.
        agent.handle(dict(_EVENT, occurred_at="2026-06-18T12:00:00Z"))

    acks = db_session.query(Message).filter_by(prompt_variant="auto_ack").count()
    assert acks == 1


@patch("src.agents.inbound.select_relevant_docs", return_value="")
@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_auto_ack_disabled(mock_send, _docs, db_session, monkeypatch):
    monkeypatch.setattr(settings, "INBOUND_AUTO_ACK_ENABLED", False)
    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=_mock_llm(), hubspot=None)
        agent.handle(dict(_EVENT))

    assert db_session.query(Message).filter_by(prompt_variant="auto_ack").count() == 0
    mock_send.assert_not_awaited()
