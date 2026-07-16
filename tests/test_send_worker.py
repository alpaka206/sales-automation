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
        direction="outgoing",
        body="Hello",
        status=status,
        scheduled_at=scheduled_at,
    )
    session.add(msg)
    session.commit()
    return msg.id


def test_claim_ready_id_approved_past(_db: Session) -> None:
    """Messages approved and scheduled in the past should be claimable."""
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    mid = _create_message(_db, "approved", past)

    claimed = send_worker._claim_ready_id()
    assert claimed == mid


def test_claim_ready_id_approved_no_scheduled(_db: Session) -> None:
    """Approved messages with no scheduled_at should be claimable (immediate)."""
    mid = _create_message(_db, "approved", None)

    claimed = send_worker._claim_ready_id()
    assert claimed == mid


def test_claim_ready_id_future_not_picked(_db: Session) -> None:
    """Messages scheduled in the future should NOT be claimed."""
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    _create_message(_db, "approved", future)

    claimed = send_worker._claim_ready_id()
    assert claimed is None


def test_claim_ready_id_pending_not_picked(_db: Session) -> None:
    """Messages still pending_approval should NOT be claimed."""
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    _create_message(_db, "pending_approval", past)

    claimed = send_worker._claim_ready_id()
    assert claimed is None


def test_claim_ready_id_marks_with_worker_token(_db: Session) -> None:
    """After claim, the row's status is set to the per-process worker token."""
    mid = _create_message(_db, "approved", None)

    send_worker._claim_ready_id()

    _db.expire_all()
    msg = _db.get(Message, mid)
    assert msg.status == send_worker._WORKER_ID
    assert msg.status.startswith("sending:")


def test_claim_is_atomic_under_concurrent_workers(_db: Session, monkeypatch) -> None:
    """Two worker IDs racing on the same row — only one should win the claim."""
    mid = _create_message(_db, "approved", None)

    original = send_worker._WORKER_ID
    monkeypatch.setattr(send_worker, "_WORKER_ID", "sending:worker-A")
    a = send_worker._claim_ready_id()

    monkeypatch.setattr(send_worker, "_WORKER_ID", "sending:worker-B")
    b = send_worker._claim_ready_id()

    monkeypatch.setattr(send_worker, "_WORKER_ID", original)

    assert a == mid
    assert b is None  # Loser sees no claimable rows.


def test_reclaim_stuck_sending(_db: Session) -> None:
    """Rows stuck in `sending:*` are reset to approved on worker start."""
    mid = _create_message(_db, "sending:dead-worker", None)

    n = send_worker._reclaim_stuck_sending()
    assert n == 1

    _db.expire_all()
    msg = _db.get(Message, mid)
    assert msg.status == "approved"


@pytest.mark.asyncio
async def test_send_one_success(_db: Session, monkeypatch) -> None:
    """Successful send transitions status from claimed → sent."""
    mid = _create_message(_db, "approved", None)
    send_worker._claim_ready_id()  # Move it to _WORKER_ID first.

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
    mid = _create_message(_db, "approved", None)
    send_worker._claim_ready_id()

    mock_send = AsyncMock(side_effect=RuntimeError("SMTP down"))
    monkeypatch.setattr("src.integrations.senders.send", mock_send)

    await send_worker._send_one(mid)

    _db.expire_all()
    msg = _db.get(Message, mid)
    assert msg.status == "send_failed"


@pytest.mark.asyncio
async def test_send_one_refuses_unclaimed(_db: Session, monkeypatch) -> None:
    """_send_one must refuse a row that this worker hasn't claimed."""
    mid = _create_message(_db, "approved", None)  # NOT claimed.
    mock_send = AsyncMock()
    monkeypatch.setattr("src.integrations.senders.send", mock_send)

    result = await send_worker._send_one(mid)

    assert result is False
    mock_send.assert_not_awaited()


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
