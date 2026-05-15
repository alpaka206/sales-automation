"""Tests for send worker rate limiting, daily cap, and jitter."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.base import Base
from src.db.models import Contact, Conversation, Message
from src.agents import send_worker


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Reset worker internal state and set rate limits for tests."""
    send_worker._sent_timestamps.clear()
    send_worker._daily_count = 0
    send_worker._daily_date = ""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(send_worker, "SessionLocal", factory)
    session = factory()
    yield session
    session.close()


def _create_approved(session: Session, n: int = 1) -> list[int]:
    """Create n approved messages, return their IDs."""
    contact = Contact(normalized_email="rate@test.com", full_name="Rate Test")
    session.add(contact)
    session.flush()
    conv = Conversation(contact_id=contact.id)
    session.add(conv)
    session.flush()

    ids = []
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    for _ in range(n):
        msg = Message(
            conversation_id=conv.id,
            direction="outbound",
            body="Hi",
            status="approved",
            scheduled_at=past,
        )
        session.add(msg)
        session.flush()
        ids.append(msg.id)
    session.commit()
    return ids


def test_daily_reset() -> None:
    """Daily counter resets when date changes."""
    send_worker._daily_count = 50
    send_worker._daily_date = "2026-05-14"

    now = datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc)
    send_worker._reset_daily_if_needed(now)

    assert send_worker._daily_count == 0
    assert send_worker._daily_date == "2026-05-15"


def test_daily_no_reset_same_day() -> None:
    """Daily counter stays if same date."""
    send_worker._daily_count = 50
    send_worker._daily_date = "2026-05-15"

    now = datetime(2026, 5, 15, 23, 0, tzinfo=timezone.utc)
    send_worker._reset_daily_if_needed(now)

    assert send_worker._daily_count == 50


def test_daily_limit_reached(monkeypatch) -> None:
    """daily_limit_reached returns True at cap."""
    monkeypatch.setattr("src.agents.send_worker.settings.DAILY_SEND_LIMIT", 10)
    send_worker._daily_count = 10
    assert send_worker._daily_limit_reached() is True


def test_daily_limit_not_reached(monkeypatch) -> None:
    monkeypatch.setattr("src.agents.send_worker.settings.DAILY_SEND_LIMIT", 10)
    send_worker._daily_count = 5
    assert send_worker._daily_limit_reached() is False


def test_daily_limit_zero_means_unlimited(monkeypatch) -> None:
    """DAILY_SEND_LIMIT=0 means no limit."""
    monkeypatch.setattr("src.agents.send_worker.settings.DAILY_SEND_LIMIT", 0)
    send_worker._daily_count = 99999
    assert send_worker._daily_limit_reached() is False


def test_minute_window_tracking(monkeypatch) -> None:
    """Per-minute rate tracking works correctly."""
    monkeypatch.setattr("src.agents.send_worker.settings.SEND_RATE_PER_MINUTE", 3)

    loop = asyncio.new_event_loop()
    now = loop.time()

    send_worker._sent_timestamps.clear()
    for _ in range(3):
        send_worker._sent_timestamps.append(now)

    with patch("asyncio.get_event_loop", return_value=loop):
        assert send_worker._minute_window_full() is True

    send_worker._sent_timestamps.clear()
    for _ in range(2):
        send_worker._sent_timestamps.append(now)

    with patch("asyncio.get_event_loop", return_value=loop):
        assert send_worker._minute_window_full() is False

    loop.close()


def test_old_timestamps_expire(monkeypatch) -> None:
    """Timestamps older than 60s are purged from the window."""
    monkeypatch.setattr("src.agents.send_worker.settings.SEND_RATE_PER_MINUTE", 3)

    loop = asyncio.new_event_loop()
    now = loop.time()

    send_worker._sent_timestamps.clear()
    for _ in range(3):
        send_worker._sent_timestamps.append(now - 61)

    with patch("asyncio.get_event_loop", return_value=loop):
        assert send_worker._minute_window_full() is False

    loop.close()


@pytest.mark.asyncio
async def test_record_send_increments_counters(monkeypatch, _reset_state) -> None:
    """_record_send increments both minute window and daily count."""
    send_worker._daily_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert send_worker._daily_count == 0

    send_worker._record_send()

    assert send_worker._daily_count == 1
    assert len(send_worker._sent_timestamps) == 1


def test_get_daily_count() -> None:
    """get_daily_count returns current count and resets if needed."""
    send_worker._daily_count = 42
    send_worker._daily_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert send_worker.get_daily_count() == 42
