"""Tests for the send queue worker."""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.base import Base
from src.db.models import Contact, Conversation, Message
from src.agents import send_worker


@pytest.fixture()
def _db(monkeypatch):
    """In-memory DB with all tables, patched into send_worker."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(send_worker, "SessionLocal", factory)
    session = factory()
    yield session
    session.close()


def _create_message(session: Session, status: str, scheduled_at: datetime | None = None) -> int:
    """Helper: create a contact, conversation, and message, return message id."""
    contact = Contact(
        normalized_email="test@example.com",
        full_name="Test User",
    )
    session.add(contact)
    session.flush()

    conv = Conversation(contact_id=contact.id)
    session.add(conv)
    session.flush()

    msg = Message(
        conversation_id=conv.id,
        direction="outbound",
        body="Hello",
        status=status,
        scheduled_at=scheduled_at,
    )
    session.add(msg)
    session.commit()
    return msg.id


def test_pick_ready_ids_approved_past(_db: Session) -> None:
    """Messages approved and scheduled in the past should be picked."""
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    mid = _create_message(_db, "approved", past)

    ids = send_worker._pick_ready_ids()
    assert mid in ids


def test_pick_ready_ids_approved_no_scheduled(_db: Session) -> None:
    """Approved messages with no scheduled_at should be picked (immediate)."""
    mid = _create_message(_db, "approved", None)

    ids = send_worker._pick_ready_ids()
    assert mid in ids


def test_pick_ready_ids_future_not_picked(_db: Session) -> None:
    """Messages scheduled in the future should NOT be picked."""
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    mid = _create_message(_db, "approved", future)

    ids = send_worker._pick_ready_ids()
    assert mid not in ids


def test_pick_ready_ids_pending_not_picked(_db: Session) -> None:
    """Messages still pending_approval should NOT be picked."""
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    mid = _create_message(_db, "pending_approval", past)

    ids = send_worker._pick_ready_ids()
    assert mid not in ids


@pytest.mark.asyncio
async def test_send_one_success(_db: Session, monkeypatch) -> None:
    """Successful send transitions status to 'sent'."""
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    mid = _create_message(_db, "approved", past)

    mock_send = AsyncMock()
    monkeypatch.setattr("src.integrations.senders.send", mock_send)

    await send_worker._send_one(mid)

    _db.expire_all()
    msg = _db.get(Message, mid)
    assert msg.status == "sent"
    assert msg.sent_at is not None
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_one_failure(_db: Session, monkeypatch) -> None:
    """Failed send transitions status to 'send_failed'."""
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    mid = _create_message(_db, "approved", past)

    mock_send = AsyncMock(side_effect=RuntimeError("SMTP down"))
    monkeypatch.setattr("src.integrations.senders.send", mock_send)

    await send_worker._send_one(mid)

    _db.expire_all()
    msg = _db.get(Message, mid)
    assert msg.status == "send_failed"


def test_migration_adds_column() -> None:
    """Migration 0006 adds scheduled_at column to messages."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    mod = importlib.import_module("src.db.migrations.0006_message_scheduled_at")
    mod.up(engine)

    from sqlalchemy import inspect as sa_inspect

    cols = {c["name"] for c in sa_inspect(engine).get_columns("messages")}
    assert "scheduled_at" in cols


def test_migration_idempotent() -> None:
    """Running migration twice does not raise."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    mod = importlib.import_module("src.db.migrations.0006_message_scheduled_at")
    mod.up(engine)
    mod.up(engine)
