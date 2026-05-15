"""Tests for the inbound poller background worker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.agents.inbound_poller import (
    POLL_MARKER_KIND,
    PROCESSED_KIND,
    _get_last_poll_at,
    _is_already_processed,
    _mark_processed,
    _save_poll_marker,
    poll_once,
)
from src.db.models import Event
from src.db.session import SessionLocal
from src.integrations.hubspot import ContactDTO


@pytest.fixture(autouse=True)
def _clean_events():
    """Remove poller-related events before and after each test."""
    with SessionLocal() as session:
        session.query(Event).filter(Event.kind.in_([POLL_MARKER_KIND, PROCESSED_KIND])).delete()
        session.commit()
    yield
    with SessionLocal() as session:
        session.query(Event).filter(Event.kind.in_([POLL_MARKER_KIND, PROCESSED_KIND])).delete()
        session.commit()


def test_get_last_poll_at_default():
    """With no marker, returns roughly 1 hour ago."""
    result = _get_last_poll_at()
    expected = datetime.now(timezone.utc) - timedelta(hours=1)
    assert abs((result - expected).total_seconds()) < 5


def test_save_and_get_poll_marker():
    """Saving a marker and reading it back gives the same timestamp."""
    ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    _save_poll_marker(ts)
    result = _get_last_poll_at()
    assert result == ts


def test_mark_and_check_processed():
    """Marking a contact as processed makes _is_already_processed return True."""
    assert not _is_already_processed("12345")
    _mark_processed("12345")
    assert _is_already_processed("12345")
    assert not _is_already_processed("99999")


@patch("src.agents.inbound_poller.HubSpotClient")
@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 1})
def test_poll_once_processes_new_contacts(mock_handle, mock_hs_cls):
    """poll_once should call handle for each new contact found."""
    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs
    mock_hs.search_contacts_sync.return_value = [
        ContactDTO(id="100", email="a@test.com", firstname="Alice", lastname="Kim"),
        ContactDTO(id="101", email="b@test.com", firstname="Bob", lastname="Lee"),
    ]

    count = poll_once()

    assert count == 2
    assert mock_handle.call_count == 2
    assert _is_already_processed("100")
    assert _is_already_processed("101")


@patch("src.agents.inbound_poller.HubSpotClient")
@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 2})
def test_poll_once_skips_already_processed(mock_handle, mock_hs_cls):
    """Contacts already processed should be skipped."""
    _mark_processed("200")

    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs
    mock_hs.search_contacts_sync.return_value = [
        ContactDTO(id="200", email="existing@test.com", firstname="Already", lastname="Done"),
        ContactDTO(id="201", email="new@test.com", firstname="New", lastname="Person"),
    ]

    count = poll_once()

    assert count == 1
    assert mock_handle.call_count == 1
    call_arg = mock_handle.call_args[0][0]
    assert call_arg["object_id"] == "201"


@patch("src.agents.inbound_poller.HubSpotClient")
def test_poll_once_hubspot_not_configured(mock_hs_cls):
    """Should return 0 when HubSpot is not configured."""
    from src.integrations.hubspot import HubSpotNotConfigured

    mock_hs_cls.side_effect = HubSpotNotConfigured("no token")
    count = poll_once()
    assert count == 0


@patch("src.agents.inbound_poller.HubSpotClient")
@patch("src.agents.inbound.InboundAgent.handle", side_effect=Exception("LLM error"))
def test_poll_once_continues_on_agent_failure(mock_handle, mock_hs_cls):
    """One contact failing should not prevent the rest from being processed."""
    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs
    mock_hs.search_contacts_sync.return_value = [
        ContactDTO(id="300", email="fail@test.com", firstname="Fail"),
        ContactDTO(id="301", email="ok@test.com", firstname="Ok"),
    ]

    # First raises, second raises too (both fail)
    mock_handle.side_effect = [Exception("LLM error"), {"message_id": 3}]

    count = poll_once()

    # Only the second one succeeds
    assert count == 1
    assert not _is_already_processed("300")
    assert _is_already_processed("301")


@patch("src.agents.inbound_poller.HubSpotClient")
def test_poll_once_saves_marker(mock_hs_cls):
    """After a poll, the marker timestamp should be updated."""
    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs
    mock_hs.search_contacts_sync.return_value = []

    poll_once()

    last = _get_last_poll_at()
    assert abs((datetime.now(timezone.utc) - last).total_seconds()) < 5
