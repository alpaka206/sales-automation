"""Tests for HubSpot inbound_status property updates."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.agents.inbound import InboundAgent
from src.api.main import app
from src.common.config import settings
from src.db.models import Contact, CustomerProfile


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"X-Internal-Token": settings.INTERNAL_API_TOKEN}


@pytest.fixture(autouse=True)
def _disable_approval_token_and_send_worker():
    with (
        patch.object(settings, "APPROVAL_REQUIRE_TOKEN", False),
        patch.object(settings, "SEND_WORKER_ENABLED", False),
    ):
        yield


# ---------- InboundAgent sets "analyzed" ----------


@patch.object(settings, "HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS", True)
@patch("src.agents.inbound.InboundAgent._finalize_draft")
@patch(
    "src.agents.inbound.InboundAgent._persist_placeholder",
        return_value=(1, 1, False),
)
@patch("src.agents.inbound.InboundAgent._draft_reply")
@patch("src.agents.inbound.InboundAgent._score", return_value=70)
@patch("src.agents.inbound.InboundAgent._classify")
@patch("src.agents.inbound.InboundAgent._fetch_contact")
@patch("src.agents.inbound.notify_approval_once")
def test_handle_sets_analyzed_status(
    mock_notify, mock_fetch, mock_classify, mock_score, mock_draft, mock_placeholder, mock_finalize
):
    """After classification, inbound_status should be updated to 'analyzed'."""
    mock_fetch.return_value = {
        "object_id": "700",
        "email": "x@test.com",
        "last_message": "I want to buy your product",
    }

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

    from src.agents.inbound import _processed

    _processed.discard("700:None")

    agent.handle({"event_type": "contact.creation", "object_id": "700"})

    hs_mock.update_inbound_status_sync.assert_called_once_with("700", "analyzed")


@patch("src.agents.inbound.InboundAgent._finalize_draft")
@patch(
    "src.agents.inbound.InboundAgent._persist_placeholder",
        return_value=(2, 2, False),
)
@patch("src.agents.inbound.InboundAgent._draft_reply")
@patch("src.agents.inbound.InboundAgent._score", return_value=60)
@patch("src.agents.inbound.InboundAgent._classify")
@patch("src.agents.inbound.InboundAgent._fetch_contact")
@patch("src.agents.inbound.notify_approval_once")
def test_handle_continues_on_status_update_failure(
    mock_notify, mock_fetch, mock_classify, mock_score, mock_draft, mock_placeholder, mock_finalize
):
    """If status update fails, handle() should still complete."""
    mock_fetch.return_value = {
        "object_id": "701",
        "email": "y@test.com",
        "last_message": "I have a question",
    }

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
    with (
        patch.object(
            InboundAgent, "_fetch_contact", return_value={"object_id": "702", "email": "z@test.com"}
        ),
        patch.object(InboundAgent, "_classify") as mock_c,
        patch.object(InboundAgent, "_score", return_value=50),
        patch.object(InboundAgent, "_pick_channel", return_value="email"),
        patch.object(InboundAgent, "_draft_reply"),
        patch.object(InboundAgent, "_persist_placeholder", return_value=(3, 3, False, False)),
        patch.object(InboundAgent, "_extract_requests"),
        patch("src.agents.inbound.notify_approval_once"),
    ):

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


# ---------- Send bookkeeping sets customer + optional HubSpot status ----------


@pytest.mark.asyncio
@patch.object(settings, "HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS", True)
@patch("src.agents.send_worker.add_progress")
@patch("src.integrations.hubspot.HubSpotClient")
async def test_send_bookkeeping_sets_meeting_link_sent(mock_hs_cls, mock_progress) -> None:
    from src.agents.send_worker import _post_send_bookkeeping

    session = MagicMock()
    contact = MagicMock(spec=Contact)
    contact.hubspot_contact_id = "hs-500"
    profile = MagicMock(spec=CustomerProfile)
    session.get.side_effect = lambda model, _id: contact if model is Contact else profile
    conv = MagicMock(id=2, contact_id=500, hubspot_ticket_id=None)
    msg = MagicMock(subject="Meeting")
    client = mock_hs_cls.return_value
    client.update_inbound_status = AsyncMock()
    client.close = AsyncMock()

    await _post_send_bookkeeping(session, msg, conv, 10)

    client.update_inbound_status.assert_awaited_once_with("hs-500", "meeting_link_sent")
    assert profile.pipeline_stage == "meeting_link_sent"
    session.commit.assert_called_once()
    mock_progress.assert_called_once()


@pytest.mark.asyncio
@patch.object(settings, "HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS", True)
@patch("src.agents.send_worker.add_progress")
@patch("src.integrations.hubspot.HubSpotClient")
async def test_send_bookkeeping_ignores_hubspot_status_failure(mock_hs_cls, _progress) -> None:
    from src.agents.send_worker import _post_send_bookkeeping

    session = MagicMock()
    contact = MagicMock(spec=Contact)
    contact.hubspot_contact_id = "hs-501"
    profile = MagicMock(spec=CustomerProfile)
    session.get.side_effect = lambda model, _id: contact if model is Contact else profile
    conv = MagicMock(id=3, contact_id=501, hubspot_ticket_id=None)
    client = mock_hs_cls.return_value
    client.update_inbound_status = AsyncMock(side_effect=RuntimeError("prop missing"))
    client.close = AsyncMock()

    await _post_send_bookkeeping(session, MagicMock(subject="Reply"), conv, 11)

    assert profile.pipeline_stage == "meeting_link_sent"
