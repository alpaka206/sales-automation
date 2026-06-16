"""Tests for the inbound poller background worker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.agents.inbound_poller import (
    TICKET_POLL_MARKER_KIND,
    TICKET_PROCESSED_KIND,
    _get_last_ticket_poll_at,
    _is_ticket_already_processed,
    _mark_ticket_processed,
    _save_ticket_poll_marker,
    poll_tickets_once,
)
from src.db.models import Event
from src.db.session import SessionLocal


_ALL_KINDS = [TICKET_POLL_MARKER_KIND, TICKET_PROCESSED_KIND]


@pytest.fixture(autouse=True)
def _clean_events():
    """Remove poller-related events before and after each test."""
    with SessionLocal() as session:
        session.query(Event).filter(Event.kind.in_(_ALL_KINDS)).delete()
        session.commit()
    yield
    with SessionLocal() as session:
        session.query(Event).filter(Event.kind.in_(_ALL_KINDS)).delete()
        session.commit()


# ---------- Ticket poll helpers ----------


def test_get_last_ticket_poll_at_default():
    """With no marker, returns roughly 1 hour ago."""
    result = _get_last_ticket_poll_at()
    expected = datetime.now(timezone.utc) - timedelta(hours=1)
    assert abs((result - expected).total_seconds()) < 5


def test_save_and_get_ticket_poll_marker():
    ts = datetime(2026, 2, 10, 8, 0, 0, tzinfo=timezone.utc)
    _save_ticket_poll_marker(ts)
    result = _get_last_ticket_poll_at()
    assert result == ts


def test_mark_and_check_ticket_processed():
    assert not _is_ticket_already_processed("T100")
    _mark_ticket_processed("T100")
    assert _is_ticket_already_processed("T100")
    assert not _is_ticket_already_processed("T999")


# ---------- poll_tickets_once ----------


@patch("src.agents.inbound_poller.HubSpotClient")
def test_poll_tickets_once_not_configured(mock_hs_cls):
    """Returns 0 when HubSpot is not configured."""
    from src.integrations.hubspot import HubSpotNotConfigured

    mock_hs_cls.side_effect = HubSpotNotConfigured("no token")
    count = poll_tickets_once()
    assert count == 0


@patch("src.agents.inbound_poller.HubSpotClient")
def test_poll_tickets_once_search_failure(mock_hs_cls):
    """When search_tickets_sync raises, returns 0."""
    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs
    mock_hs.search_tickets_sync.side_effect = Exception("API error")

    count = poll_tickets_once()
    assert count == 0


@patch("src.agents.inbound_poller.HubSpotClient")
@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 10})
def test_poll_tickets_once_processes_ticket(mock_handle, mock_hs_cls):
    """Happy path: discovers a ticket, resolves its contact, processes."""
    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs

    ticket = MagicMock()
    ticket.id = "T500"
    mock_hs.search_tickets_sync.return_value = [ticket]
    mock_hs.get_ticket_primary_contact_sync.return_value = "C600"

    count = poll_tickets_once()

    assert count == 1
    mock_handle.assert_called_once()
    assert _is_ticket_already_processed("T500")


@patch("src.agents.inbound_poller.HubSpotClient")
def test_poll_tickets_once_no_contact(mock_hs_cls):
    """Ticket with no associated contact is marked processed and skipped."""
    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs

    ticket = MagicMock()
    ticket.id = "T501"
    mock_hs.search_tickets_sync.return_value = [ticket]
    mock_hs.get_ticket_primary_contact_sync.return_value = None

    count = poll_tickets_once()

    assert count == 0
    assert _is_ticket_already_processed("T501")


@patch("src.agents.inbound_poller.HubSpotClient")
def test_poll_tickets_once_contact_lookup_fails(mock_hs_cls):
    """If contact lookup fails, ticket is skipped (not marked processed)."""
    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs

    ticket = MagicMock()
    ticket.id = "T502"
    mock_hs.search_tickets_sync.return_value = [ticket]
    mock_hs.get_ticket_primary_contact_sync.side_effect = Exception("lookup error")

    count = poll_tickets_once()

    assert count == 0
    assert not _is_ticket_already_processed("T502")


@patch("src.agents.inbound_poller.HubSpotClient")
def test_poll_tickets_once_skips_already_processed(mock_hs_cls):
    """Already-processed tickets are skipped."""
    _mark_ticket_processed("T503")

    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs

    ticket = MagicMock()
    ticket.id = "T503"
    mock_hs.search_tickets_sync.return_value = [ticket]

    count = poll_tickets_once()
    assert count == 0
