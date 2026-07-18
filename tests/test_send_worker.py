"""Tests for the send queue worker."""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, text
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


def _create_message(
    session: Session,
    status: str,
    scheduled_at: datetime | None = None,
    send_claimed_at: datetime | None = None,
    prompt_variant: str | None = None,
) -> int:
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
        send_claimed_at=send_claimed_at,
        prompt_variant=prompt_variant,
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
    assert msg.send_claimed_at is not None
    assert msg.send_attempts == 1


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
    """Expired sends are quarantined because SMTP delivery may have succeeded."""
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=send_worker.SEND_LEASE_SECONDS + 1)
    mid = _create_message(_db, "sending:dead-worker", None, stale)

    n = send_worker._reclaim_stuck_sending(now)
    assert n == 1

    _db.expire_all()
    msg = _db.get(Message, mid)
    assert msg.status == "delivery_unknown"
    assert msg.send_claimed_at is None


def test_reclaim_keeps_live_sending_lease(_db: Session) -> None:
    now = datetime.now(timezone.utc)
    mid = _create_message(_db, "sending:live-worker", None, now)

    assert send_worker._reclaim_stuck_sending(now) == 0

    _db.expire_all()
    assert _db.get(Message, mid).status == "sending:live-worker"


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
async def test_auto_ack_transient_failure_is_requeued(_db: Session, monkeypatch) -> None:
    from src.integrations.senders.smtp import SMTPTransientError

    mid = _create_message(_db, "approved", None, prompt_variant="auto_ack")
    send_worker._claim_ready_id()
    monkeypatch.setattr(
        "src.integrations.senders.send",
        AsyncMock(side_effect=SMTPTransientError("temporary")),
    )
    monkeypatch.setattr(send_worker.asyncio, "sleep", AsyncMock())

    assert await send_worker._send_one(mid) is False

    _db.expire_all()
    msg = _db.get(Message, mid)
    assert msg.status == "approved"
    assert msg.scheduled_at is not None
    assert msg.send_attempts == 1


@pytest.mark.asyncio
async def test_unknown_smtp_outcome_is_quarantined_without_retry(
    _db: Session, monkeypatch
) -> None:
    from src.integrations.senders.smtp import SMTPDeliveryUnknown

    mid = _create_message(_db, "approved", None)
    send_worker._claim_ready_id()
    send = AsyncMock(side_effect=SMTPDeliveryUnknown("ambiguous"))
    monkeypatch.setattr("src.integrations.senders.send", send)

    assert await send_worker._send_one(mid) is False
    _db.expire_all()
    assert _db.get(Message, mid).status == "delivery_unknown"
    assert _db.get(Message, mid).smtp_message_id is not None
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_auto_ack_transient_retry_is_bounded(_db: Session, monkeypatch) -> None:
    from src.integrations.senders.smtp import SMTPTransientError

    mid = _create_message(_db, "approved", None, prompt_variant="auto_ack")
    msg = _db.get(Message, mid)
    msg.send_attempts = send_worker.AUTO_ACK_QUEUE_MAX_ATTEMPTS - 1
    _db.commit()
    send_worker._claim_ready_id()
    monkeypatch.setattr(
        "src.integrations.senders.send",
        AsyncMock(side_effect=SMTPTransientError("temporary")),
    )
    monkeypatch.setattr(send_worker.asyncio, "sleep", AsyncMock())

    assert await send_worker._send_one(mid) is False

    _db.expire_all()
    assert _db.get(Message, mid).status == "send_failed"


@pytest.mark.asyncio
async def test_auto_ack_success_does_not_advance_pipeline(_db: Session, monkeypatch) -> None:
    mid = _create_message(_db, "approved", None, prompt_variant="auto_ack")
    send_worker._claim_ready_id()
    monkeypatch.setattr("src.integrations.senders.send", AsyncMock())
    bookkeeping = AsyncMock()
    monkeypatch.setattr(send_worker, "_post_send_bookkeeping", bookkeeping)

    assert await send_worker._send_one(mid) is True

    _db.expire_all()
    msg = _db.get(Message, mid)
    assert msg.status == "sent"
    assert msg.post_send_synced_at is not None
    assert msg.conversation.stage != "meeting_link_sent"
    bookkeeping.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_send_failure_never_marks_delivered_email_failed(
    _db: Session, monkeypatch
) -> None:
    mid = _create_message(_db, "approved", None)
    send_worker._claim_ready_id()
    monkeypatch.setattr("src.integrations.senders.send", AsyncMock())
    monkeypatch.setattr(
        send_worker,
        "_post_send_bookkeeping",
        AsyncMock(side_effect=RuntimeError("Sheets unavailable")),
    )

    assert await send_worker._send_one(mid) is True

    _db.expire_all()
    assert _db.get(Message, mid).status == "sent"


@pytest.mark.asyncio
async def test_post_send_sync_retry_does_not_resend_email(
    _db: Session, monkeypatch
) -> None:
    mid = _create_message(_db, "sent", None)
    msg = _db.get(Message, mid)
    msg.post_send_sync_attempts = 1
    msg.post_send_sync_attempted_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    msg.post_send_sync_error = "google_sheets_stage"
    _db.commit()
    mock_bookkeeping = AsyncMock()
    monkeypatch.setattr(send_worker, "_post_send_bookkeeping", mock_bookkeeping)

    assert await send_worker._retry_post_send_syncs() == 1

    mock_bookkeeping.assert_awaited_once()


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


def test_send_lease_migration_adds_columns_and_backfills_sent() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE messages ("
                "id INTEGER PRIMARY KEY, status VARCHAR NOT NULL, sent_at TIMESTAMP)"
            )
        )
        conn.execute(text("INSERT INTO messages VALUES (1, 'sent', CURRENT_TIMESTAMP)"))

    mod = importlib.import_module("src.db.migrations.0031_message_send_lease")
    mod.up(engine)
    mod.up(engine)

    from sqlalchemy import inspect as sa_inspect

    cols = {c["name"] for c in sa_inspect(engine).get_columns("messages")}
    assert {
        "send_claimed_at",
        "post_send_synced_at",
        "post_send_sync_attempted_at",
        "post_send_sync_attempts",
        "post_send_sync_error",
    } <= cols
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT post_send_synced_at FROM messages WHERE id = 1")
        ).scalar_one() is not None


def test_delivery_retry_migration_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE messages (id INTEGER PRIMARY KEY, status VARCHAR NOT NULL)")
        )

    mod = importlib.import_module("src.db.migrations.0034_delivery_retries")
    mod.up(engine)
    mod.up(engine)

    from sqlalchemy import inspect as sa_inspect

    cols = {c["name"] for c in sa_inspect(engine).get_columns("messages")}
    assert {
        "send_attempts",
        "slack_notification_attempted_at",
        "slack_notification_attempts",
    } <= cols
