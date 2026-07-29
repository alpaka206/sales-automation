"""Operator recovery actions keep ambiguous delivery explicit and audited."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db.models import Contact, Conversation, Event, InboundJob, Message


@pytest.fixture()
def recovery_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with patch("src.api.web.routes.recovery.SessionLocal", factory):
        yield factory


def _seed(factory, status: str) -> tuple[int, int]:
    with factory() as session:
        contact = Contact(normalized_email="recovery@example.com", full_name="Recovery")
        session.add(contact)
        session.flush()
        conversation = Conversation(contact_id=contact.id, stage="new")
        session.add(conversation)
        session.flush()
        message = Message(
            conversation_id=conversation.id,
            direction="outgoing",
            channel="email",
            body="reply",
            status=status,
        )
        session.add(message)
        session.flush()
        job = InboundJob(
            event_key="recovery-job",
            source="test",
            payload={"ticket_id": "T-1"},
            status="dead",
            attempts=8,
            last_error="boom",
        )
        session.add(job)
        session.commit()
        return message.id, job.id


def test_recovery_console_lists_failures(recovery_db) -> None:
    message_id, _job_id = _seed(recovery_db, "send_failed")
    with TestClient(app) as client:
        response = client.get("/operations/recovery")
    assert response.status_code == 200
    assert f"#{message_id}" in response.text


def test_old_recovery_url_redirects_into_the_operations_screen(recovery_db) -> None:
    """The console moved into /logs; bookmarks and the audit trail's links still work."""
    with TestClient(app) as client:
        response = client.get("/operations/recovery", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/logs?tab=recovery"


def test_operations_screen_defaults_to_the_recovery_tab(recovery_db) -> None:
    """Recovery is the tab with work on it; logs are for diagnosing what it shows."""
    message_id, _job_id = _seed(recovery_db, "send_failed")
    with TestClient(app) as client:
        response = client.get("/logs")
    assert response.status_code == 200
    assert f"#{message_id}" in response.text
    assert "발송 문제" in response.text


def test_log_tab_still_renders_and_keeps_the_recovery_count_visible(recovery_db) -> None:
    """A failure arriving while you read logs must not be invisible."""
    _seed(recovery_db, "send_failed")
    with TestClient(app) as client:
        response = client.get("/logs?tab=log")
    assert response.status_code == 200
    assert "시각(KST)" in response.text  # the log table
    assert "복구 대상" in response.text  # the tab strip, with its count


def test_operations_screen_is_reachable_without_a_session_user(recovery_db) -> None:
    """Basic/localhost mode has no users, so role=="admin" can never be true there.

    /logs demanded exactly that while the sidebar kept offering the link, so the page
    403'd for every local operator. Merging recovery in would have taken that with it.
    """
    with TestClient(app) as client:
        assert client.get("/logs").status_code == 200


def test_failed_message_can_be_requeued(recovery_db) -> None:
    message_id, _job_id = _seed(recovery_db, "send_failed")
    with TestClient(app) as client:
        response = client.post(
            f"/operations/recovery/messages/{message_id}/retry",
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    with recovery_db() as session:
        assert session.get(Message, message_id).status == "approved"
        assert session.scalar(select(Event).where(Event.kind == "operator_recovery"))


def test_unknown_delivery_requires_explicit_resolution(recovery_db) -> None:
    message_id, _job_id = _seed(recovery_db, "delivery_unknown")
    with TestClient(app) as client:
        response = client.post(
            f"/operations/recovery/messages/{message_id}/resolve",
            data={"action": "confirmed_sent"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    with recovery_db() as session:
        message = session.get(Message, message_id)
        assert message.status == "sent"
        assert message.sent_at is not None
        assert message.conversation.stage == "meeting_link_sent"


def test_dead_inbound_job_can_be_requeued(recovery_db) -> None:
    _message_id, job_id = _seed(recovery_db, "send_failed")
    with TestClient(app) as client:
        response = client.post(
            f"/operations/recovery/inbound/{job_id}/retry",
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    with recovery_db() as session:
        job = session.get(InboundJob, job_id)
        assert job.status == "pending"
        assert job.attempts == 0
        assert job.last_error is None
