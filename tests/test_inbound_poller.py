"""Tests for the inbound poller background worker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.agents.inbound_poller import (
    POLL_BATCH_SIZE,
    TICKET_POLL_MARKER_KIND,
    TICKET_PROCESSED_KIND,
    _get_last_ticket_poll_at,
    _is_ticket_already_processed,
    _mark_ticket_processed,
    _save_ticket_poll_marker,
    poll_tickets_once,
)
from src.agents.inbound_worker import enqueue_inbound_ticket
from src.common.config import settings
from src.db.models import Event, InboundJob
from src.db.session import SessionLocal


_ALL_KINDS = [TICKET_POLL_MARKER_KIND, TICKET_PROCESSED_KIND]


@pytest.fixture(autouse=True)
def _clean_events():
    """Remove poller-related events before and after each test."""
    with SessionLocal() as session:
        session.query(Event).filter(Event.kind.in_(_ALL_KINDS)).delete()
        session.query(InboundJob).delete()
        session.commit()
    yield
    with SessionLocal() as session:
        session.query(Event).filter(Event.kind.in_(_ALL_KINDS)).delete()
        session.query(InboundJob).delete()
        session.commit()


# ---------- Ticket poll helpers ----------


def test_get_last_ticket_poll_at_default():
    """With no marker, uses the configurable initial lookback."""
    result = _get_last_ticket_poll_at()
    expected = datetime.now(timezone.utc) - timedelta(
        hours=settings.INBOUND_INITIAL_LOOKBACK_HOURS
    )
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
    """Happy path persists work without running AI in the polling request."""
    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs

    ticket = MagicMock()
    ticket.id = "T500"
    mock_hs.search_tickets_sync.return_value = [ticket]
    mock_hs.get_ticket_primary_contact_sync.return_value = "C600"

    count = poll_tickets_once()

    assert count == 1
    mock_handle.assert_not_called()
    with SessionLocal() as session:
        assert session.query(InboundJob).filter_by(event_key="hubspot:ticket:T500:created").one()


@patch("src.agents.inbound_poller.HubSpotClient")
def test_poll_tickets_once_does_not_lookup_contact(mock_hs_cls):
    """Contact lookup is deferred to the retryable worker."""
    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs

    ticket = MagicMock()
    ticket.id = "T501"
    mock_hs.search_tickets_sync.return_value = [ticket]
    mock_hs.get_ticket_primary_contact_sync.return_value = None

    count = poll_tickets_once()

    assert count == 1
    mock_hs.get_ticket_primary_contact_sync.assert_not_called()


@patch("src.agents.inbound_poller.HubSpotClient")
def test_poll_tickets_once_dedupes_existing_job(mock_hs_cls):
    """Webhook/poller overlap is removed by the shared stable event key."""
    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs

    ticket = MagicMock()
    ticket.id = "T502"
    mock_hs.search_tickets_sync.return_value = [ticket]
    enqueue_inbound_ticket("T502", source="webhook")

    count = poll_tickets_once()

    assert count == 0


@patch("src.agents.inbound_poller.HubSpotClient")
def test_poll_tickets_once_skips_already_queued(mock_hs_cls):
    """A repeated overlap window does not enqueue a second row."""
    enqueue_inbound_ticket("T503", source="webhook")

    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs

    ticket = MagicMock()
    ticket.id = "T503"
    mock_hs.search_tickets_sync.return_value = [ticket]

    count = poll_tickets_once()
    assert count == 0


@patch("src.agents.inbound_poller.enqueue_inbound_ticket", return_value=True)
@patch("src.agents.inbound_poller.HubSpotClient")
def test_poll_tickets_once_drains_more_than_one_page(mock_hs_cls, mock_enqueue):
    """A full search result advances by modification time instead of dropping overflow."""
    mock_hs = MagicMock()
    mock_hs_cls.return_value = mock_hs
    base = datetime(2026, 7, 18, tzinfo=timezone.utc)
    first_page = []
    for index in range(POLL_BATCH_SIZE):
        ticket = MagicMock()
        ticket.id = f"bulk-{index}"
        ticket.updated_at = base + timedelta(milliseconds=index + 1)
        first_page.append(ticket)
    final_ticket = MagicMock()
    final_ticket.id = "bulk-final"
    final_ticket.updated_at = base + timedelta(seconds=2)
    mock_hs.search_tickets_sync.side_effect = [first_page, [final_ticket]]

    # Anchor the poll cursor just behind the fixture data. Without a marker the
    # cursor derives from the real clock (now - initial lookback); once real time
    # advances past `base`, the first page's timestamps fall before the cursor and
    # this drain-advance path is never exercised — a time-bomb that fails the test
    # on any run after base + lookback. Production always has a recent marker.
    _save_ticket_poll_marker(base - timedelta(minutes=30))

    assert poll_tickets_once() == POLL_BATCH_SIZE + 1
    assert mock_hs.search_tickets_sync.call_count == 2
    second_after = mock_hs.search_tickets_sync.call_args_list[1].kwargs["created_after"]
    assert second_after == first_page[-1].updated_at
    assert mock_enqueue.call_count == POLL_BATCH_SIZE + 1


@patch("src.agents.inbound_poller.enqueue_inbound_ticket", side_effect=RuntimeError("db down"))
@patch("src.agents.inbound_poller.HubSpotClient")
def test_poll_failure_does_not_advance_watermark(mock_hs_cls, _mock_enqueue):
    marker = datetime(2026, 7, 17, tzinfo=timezone.utc)
    _save_ticket_poll_marker(marker)
    ticket = MagicMock()
    ticket.id = "retry-me"
    ticket.updated_at = datetime(2026, 7, 18, tzinfo=timezone.utc)
    mock_hs_cls.return_value.search_tickets_sync.return_value = [ticket]

    assert poll_tickets_once() == 0
    assert _get_last_ticket_poll_at() == marker
