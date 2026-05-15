"""Tests for inbound message body fetching priority (form → email → note → fallback)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents.inbound import InboundAgent
from src.integrations.hubspot import ContactDTO


def _make_agent(hubspot_mock: MagicMock) -> InboundAgent:
    """Create an InboundAgent with mocked LLM and HubSpot."""
    agent = InboundAgent.__new__(InboundAgent)
    agent.llm = MagicMock()
    agent.hubspot = hubspot_mock
    return agent


def _base_event(object_id: str = "500", **overrides) -> dict:
    return {
        "event_type": "contact.creation",
        "object_id": object_id,
        "email": "test@example.com",
        "full_name": "Test User",
        "company": "TestCo",
        "country": "KR",
        "last_message": "",
        **overrides,
    }


def _mock_hubspot_basic(mock: MagicMock) -> None:
    """Set up basic hubspot mock responses."""
    mock.get_contact_sync.return_value = ContactDTO(
        id="500", email="test@example.com", firstname="Test", lastname="User",
    )
    mock.get_recent_emails_sync.return_value = []
    mock.get_associated_deals_sync.return_value = []


def test_form_submission_takes_priority():
    """When form submission exists, it should be used for last_message."""
    hs = MagicMock()
    _mock_hubspot_basic(hs)
    hs.get_latest_form_submission.return_value = "문의합니다: 가격이 얼마인가요?"
    hs.get_latest_inbound_email.return_value = "이전 이메일 내용"
    hs.get_latest_note.return_value = "노트 내용"

    agent = _make_agent(hs)
    info = agent._fetch_contact(_base_event())

    assert info["last_message"] == "문의합니다: 가격이 얼마인가요?"
    assert info["inbound_source"] == "form_submission"
    hs.get_latest_inbound_email.assert_not_called()
    hs.get_latest_note.assert_not_called()


def test_inbound_email_when_no_form():
    """When no form but inbound email exists, it should be used."""
    hs = MagicMock()
    _mock_hubspot_basic(hs)
    hs.get_latest_form_submission.return_value = None
    hs.get_latest_inbound_email.return_value = "안녕하세요, 서비스에 관심이 있습니다."
    hs.get_latest_note.return_value = "노트 내용"

    agent = _make_agent(hs)
    info = agent._fetch_contact(_base_event())

    assert info["last_message"] == "안녕하세요, 서비스에 관심이 있습니다."
    assert info["inbound_source"] == "inbound_email"
    hs.get_latest_note.assert_not_called()


def test_note_when_no_form_or_email():
    """When no form or email but note exists, it should be used."""
    hs = MagicMock()
    _mock_hubspot_basic(hs)
    hs.get_latest_form_submission.return_value = None
    hs.get_latest_inbound_email.return_value = None
    hs.get_latest_note.return_value = "영업팀 전달: 이 고객이 전화 문의함"

    agent = _make_agent(hs)
    info = agent._fetch_contact(_base_event())

    assert info["last_message"] == "영업팀 전달: 이 고객이 전화 문의함"
    assert info["inbound_source"] == "note"


def test_event_payload_fallback():
    """When event has last_message already set, skip HubSpot body fetch."""
    hs = MagicMock()
    _mock_hubspot_basic(hs)

    agent = _make_agent(hs)
    info = agent._fetch_contact(_base_event(last_message="이벤트에서 온 메시지"))

    assert info["last_message"] == "이벤트에서 온 메시지"
    assert info["inbound_source"] == "event_payload"
    hs.get_latest_form_submission.assert_not_called()


def test_empty_message_warning():
    """When no message body can be found anywhere, inbound_source should be 'none'."""
    hs = MagicMock()
    _mock_hubspot_basic(hs)
    hs.get_latest_form_submission.return_value = None
    hs.get_latest_inbound_email.return_value = None
    hs.get_latest_note.return_value = None

    agent = _make_agent(hs)
    info = agent._fetch_contact(_base_event())

    assert info["last_message"] == ""
    assert info["inbound_source"] == "event_payload"


def test_form_fetch_error_falls_through():
    """If form submission fetch raises, should try inbound email next."""
    hs = MagicMock()
    _mock_hubspot_basic(hs)
    hs.get_latest_form_submission.side_effect = Exception("API error")
    hs.get_latest_inbound_email.return_value = "이메일 본문"
    hs.get_latest_note.return_value = None

    agent = _make_agent(hs)
    info = agent._fetch_contact(_base_event())

    assert info["last_message"] == "이메일 본문"
    assert info["inbound_source"] == "inbound_email"


def test_no_hubspot_client_with_message():
    """Without HubSpot client and a message present, source is event_payload."""
    agent = InboundAgent.__new__(InboundAgent)
    agent.llm = MagicMock()
    agent.hubspot = None

    info = agent._fetch_contact(_base_event(last_message="직접 입력"))

    assert info["last_message"] == "직접 입력"
    assert info.get("inbound_source") == "event_payload"


def test_no_hubspot_client_no_message():
    """Without HubSpot client and no message, source is none."""
    agent = InboundAgent.__new__(InboundAgent)
    agent.llm = MagicMock()
    agent.hubspot = None

    info = agent._fetch_contact(_base_event())

    assert info["last_message"] == ""
    assert info.get("inbound_source") == "none"
