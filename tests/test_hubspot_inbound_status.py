"""Tests for HubSpot inbound_status property updates."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.agents.inbound import InboundAgent
from src.api.main import app
from src.common.config import settings
from src.db.models import Event
from src.db.session import SessionLocal
from src.integrations.hubspot import ContactDTO


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"X-Internal-Token": settings.INTERNAL_API_TOKEN}


# ---------- InboundAgent sets "analyzed" ----------


@patch("src.agents.inbound.InboundAgent._persist", return_value=1)
@patch("src.agents.inbound.InboundAgent._draft_reply")
@patch("src.agents.inbound.InboundAgent._score", return_value=70)
@patch("src.agents.inbound.InboundAgent._classify")
@patch("src.agents.inbound.InboundAgent._fetch_contact")
@patch("src.agents.inbound.notify_approval")
def test_handle_sets_analyzed_status(
    mock_notify, mock_fetch, mock_classify, mock_score, mock_draft, mock_persist
):
    """After classification, inbound_status should be updated to 'analyzed'."""
    mock_fetch.return_value = {"object_id": "700", "email": "x@test.com"}

    mock_classify_result = MagicMock()
    mock_classify_result.category = "purchase_inquiry"
    mock_classify.return_value = mock_classify_result

    mock_draft_result = MagicMock()
    mock_draft_result.subject = "Re: Inquiry"
    mock_draft_result.body = "Thank you"
    mock_draft.return_value = mock_draft_result

    hs_mock = MagicMock()
    agent = InboundAgent.__new__(InboundAgent)
    agent.llm = MagicMock()
    agent.hubspot = hs_mock

    # Clear dedup set
    from src.agents.inbound import _processed
    _processed.discard("700:None")

    agent.handle({"event_type": "contact.creation", "object_id": "700"})

    hs_mock.update_inbound_status_sync.assert_called_once_with("700", "analyzed")


@patch("src.agents.inbound.InboundAgent._persist", return_value=2)
@patch("src.agents.inbound.InboundAgent._draft_reply")
@patch("src.agents.inbound.InboundAgent._score", return_value=60)
@patch("src.agents.inbound.InboundAgent._classify")
@patch("src.agents.inbound.InboundAgent._fetch_contact")
@patch("src.agents.inbound.notify_approval")
def test_handle_continues_on_status_update_failure(
    mock_notify, mock_fetch, mock_classify, mock_score, mock_draft, mock_persist
):
    """If status update fails, handle() should still complete."""
    mock_fetch.return_value = {"object_id": "701", "email": "y@test.com"}

    mock_classify_result = MagicMock()
    mock_classify_result.category = "general_inquiry"
    mock_classify.return_value = mock_classify_result

    mock_draft_result = MagicMock()
    mock_draft_result.subject = "Re: Question"
    mock_draft_result.body = "Hello"
    mock_draft.return_value = mock_draft_result

    hs_mock = MagicMock()
    hs_mock.update_inbound_status_sync.side_effect = Exception("API error")
    agent = InboundAgent.__new__(InboundAgent)
    agent.llm = MagicMock()
    agent.hubspot = hs_mock

    from src.agents.inbound import _processed
    _processed.discard("701:None")

    result = agent.handle({"event_type": "contact.creation", "object_id": "701"})

    assert result is not None
    assert result["message_id"] == 2


def test_no_hubspot_skips_status_update():
    """Without HubSpot client, status update should be skipped."""
    with patch.object(InboundAgent, "_fetch_contact", return_value={"object_id": "702", "email": "z@test.com"}), \
         patch.object(InboundAgent, "_classify") as mock_c, \
         patch.object(InboundAgent, "_score", return_value=50), \
         patch.object(InboundAgent, "_pick_channel", return_value="email"), \
         patch.object(InboundAgent, "_draft_reply"), \
         patch.object(InboundAgent, "_persist", return_value=3), \
         patch("src.agents.inbound.notify_approval"):

        mock_classify_result = MagicMock()
        mock_classify_result.category = "support"
        mock_c.return_value = mock_classify_result

        agent = InboundAgent.__new__(InboundAgent)
        agent.llm = MagicMock()
        agent.hubspot = None

        from src.agents.inbound import _processed
        _processed.discard("702:None")

        result = agent.handle({"event_type": "contact.creation", "object_id": "702"})
        assert result is not None


# ---------- Approve endpoint sets "meeting_link_sent" ----------


@patch("src.integrations.hubspot.HubSpotClient.update_inbound_status", new_callable=AsyncMock)
@patch("src.integrations.hubspot.HubSpotClient.create_email_engagement", new_callable=AsyncMock, return_value="eng-1")
@patch("src.integrations.hubspot.HubSpotClient.close", new_callable=AsyncMock)
@patch("src.integrations.senders.send", new_callable=AsyncMock)
@patch("src.api.main.approve")
@patch("src.api.main.mark_sent")
def test_approve_sets_meeting_link_sent(
    mock_mark_sent, mock_approve, mock_send, mock_close, mock_engagement, mock_status, client
):
    """After sending, approve endpoint should update inbound_status to meeting_link_sent."""
    msg_mock = MagicMock()
    msg_mock.id = 10
    msg_mock.status = "approved"
    msg_mock.subject = "Meeting"
    msg_mock.body = "Let's meet"
    msg_mock.conversation.contact_id = 500
    mock_approve.return_value = msg_mock

    r = client.post(
        "/approve/10",
        json={"approver": "user:1", "action": "approve"},
        headers=_auth_headers(),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "sent"
    mock_status.assert_called_once_with("500", "meeting_link_sent")


@patch("src.integrations.hubspot.HubSpotClient.update_inbound_status", new_callable=AsyncMock, side_effect=Exception("prop missing"))
@patch("src.integrations.hubspot.HubSpotClient.create_email_engagement", new_callable=AsyncMock, return_value="eng-2")
@patch("src.integrations.hubspot.HubSpotClient.close", new_callable=AsyncMock)
@patch("src.integrations.senders.send", new_callable=AsyncMock)
@patch("src.api.main.approve")
@patch("src.api.main.mark_sent")
def test_approve_queues_retry_on_status_failure(
    mock_mark_sent, mock_approve, mock_send, mock_close, mock_engagement, mock_status, client
):
    """If status update fails, send still succeeds and failure is queued for retry."""
    msg_mock = MagicMock()
    msg_mock.id = 11
    msg_mock.status = "approved"
    msg_mock.subject = "Re: Inquiry"
    msg_mock.body = "Response"
    msg_mock.conversation.contact_id = 501
    mock_approve.return_value = msg_mock

    r = client.post(
        "/approve/11",
        json={"approver": "user:2", "action": "approve"},
        headers=_auth_headers(),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "sent"

    with SessionLocal() as session:
        retry = (
            session.query(Event)
            .filter(Event.kind == "hubspot_status_update_failed")
            .order_by(Event.id.desc())
            .first()
        )
        assert retry is not None
        assert retry.payload["contact_id"] == "501"
        assert retry.payload["target_status"] == "meeting_link_sent"
        session.delete(retry)
        session.commit()
