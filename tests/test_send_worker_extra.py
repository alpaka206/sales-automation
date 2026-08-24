"""Additional tests for send_worker — rate limiting, daily cap, reset."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.agents import send_worker


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset worker state between tests."""
    send_worker._sent_timestamps.clear()
    send_worker._daily_count = 0
    send_worker._daily_date = ""
    yield
    send_worker._sent_timestamps.clear()
    send_worker._daily_count = 0
    send_worker._daily_date = ""


def test_reset_daily_if_needed_new_day() -> None:
    send_worker._daily_count = 100
    send_worker._daily_date = "2020-01-01"
    send_worker._reset_daily_if_needed()
    assert send_worker._daily_count == 0


def test_reset_daily_if_needed_same_day() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    send_worker._daily_date = today
    send_worker._daily_count = 5
    send_worker._reset_daily_if_needed()
    assert send_worker._daily_count == 5


def test_daily_limit_reached_false() -> None:
    with patch.object(send_worker.settings, "DAILY_SEND_LIMIT", 100):
        send_worker._daily_count = 50
        assert send_worker._daily_limit_reached() is False


def test_daily_limit_reached_true() -> None:
    with patch.object(send_worker.settings, "DAILY_SEND_LIMIT", 10):
        send_worker._daily_count = 10
        assert send_worker._daily_limit_reached() is True


def test_daily_limit_disabled() -> None:
    with patch.object(send_worker.settings, "DAILY_SEND_LIMIT", 0):
        send_worker._daily_count = 99999
        assert send_worker._daily_limit_reached() is False


def test_get_daily_count_resets_on_new_day() -> None:
    send_worker._daily_date = "2020-01-01"
    send_worker._daily_count = 999
    count = send_worker.get_daily_count()
    assert count == 0


@pytest.mark.asyncio
async def test_send_one_skips_non_approved() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.db.base import Base
    from src.db.models import Contact, Conversation, Message

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    session = factory()
    contact = Contact(normalized_email="x@y.com", full_name="Test")
    session.add(contact)
    session.flush()
    conv = Conversation(contact_id=contact.id)
    session.add(conv)
    session.flush()
    msg = Message(
        conversation_id=conv.id,
        direction="outgoing",
        body="Hello",
        status="pending_approval",
    )
    session.add(msg)
    session.commit()
    msg_id = msg.id
    session.close()

    with patch.object(send_worker, "SessionLocal", factory):
        result = await send_worker._send_one(msg_id)

    assert result is False


# ---------- request_shutdown ----------


def test_request_shutdown():
    """request_shutdown sets the global _shutdown flag."""
    send_worker._shutdown = False
    send_worker.request_shutdown()
    assert send_worker._shutdown is True
    send_worker._shutdown = False


# ---------- Delivery error handling ----------


@pytest.mark.asyncio
async def test_send_one_permanent_error() -> None:
    """DeliveryPermanentError fails immediately without retry."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from unittest.mock import AsyncMock

    from src.db.base import Base
    from src.db.models import Contact, Conversation, Message
    from src.integrations.delivery import DeliveryPermanentError

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    session = factory()
    contact = Contact(normalized_email="perm@test.com", full_name="Perm")
    session.add(contact)
    session.flush()
    conv = Conversation(contact_id=contact.id)
    session.add(conv)
    session.flush()
    msg = Message(
        conversation_id=conv.id,
        direction="outgoing",
        body="Hello",
        status="approved",
    )
    session.add(msg)
    session.commit()
    msg_id = msg.id
    session.close()

    with patch.object(send_worker, "SessionLocal", factory):
        send_worker._claim_ready_id()
        mock_send = AsyncMock(side_effect=DeliveryPermanentError("invalid recipient"))
        with patch("src.integrations.senders.send", mock_send):
            result = await send_worker._send_one(msg_id)

    assert result is False
    mock_send.assert_awaited_once()
    s = factory()
    m = s.get(Message, msg_id)
    assert m.status == "send_failed"
    s.close()


@pytest.mark.asyncio
async def test_send_one_transient_error_retries() -> None:
    """DeliveryTransientError retries up to the configured maximum."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from unittest.mock import AsyncMock

    from src.db.base import Base
    from src.db.models import Contact, Conversation, Message
    from src.integrations.delivery import DeliveryTransientError

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    session = factory()
    contact = Contact(normalized_email="trans@test.com", full_name="Trans")
    session.add(contact)
    session.flush()
    conv = Conversation(contact_id=contact.id)
    session.add(conv)
    session.flush()
    msg = Message(
        conversation_id=conv.id,
        direction="outgoing",
        body="Hello",
        status="approved",
    )
    session.add(msg)
    session.commit()
    msg_id = msg.id
    session.close()

    with patch.object(send_worker, "SessionLocal", factory):
        send_worker._claim_ready_id()
        mock_send = AsyncMock(side_effect=DeliveryTransientError("429 try later"))
        with patch("src.integrations.senders.send", mock_send):
            result = await send_worker._send_one(msg_id)

    assert result is False
    assert mock_send.await_count == send_worker.SEND_TRANSIENT_MAX_RETRIES
    s = factory()
    m = s.get(Message, msg_id)
    assert m.status == "send_failed"
    s.close()
