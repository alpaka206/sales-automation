"""Tests for approval flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agents.approval import approve, reject, mark_sent, ApprovalError
from src.db.base import Base
from src.db.models import Approval, Contact, Conversation, Message


@pytest.fixture()
def db_with_message():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    contact = Contact(normalized_email="a@b.com", full_name="Test")
    session.add(contact)
    session.flush()

    conv = Conversation(contact_id=contact.id)
    session.add(conv)
    session.flush()

    msg = Message(
        conversation_id=conv.id,
        direction="outbound",
        body="Draft body",
        status="pending_approval",
    )
    session.add(msg)
    session.commit()

    yield session, Session, msg.id
    session.close()


def test_approve_flips_status(db_with_message) -> None:
    session, Session, msg_id = db_with_message

    with patch("src.agents.approval.SessionLocal", return_value=session):
        result = approve(msg_id, approver="slack:U001")

    assert result.status == "approved"
    assert result.approved_by == "slack:U001"
    assert result.approved_at is not None

    approvals = session.query(Approval).all()
    assert len(approvals) == 1
    assert approvals[0].action == "approve"


def test_approve_with_edit(db_with_message) -> None:
    session, Session, msg_id = db_with_message

    with patch("src.agents.approval.SessionLocal", return_value=session):
        result = approve(msg_id, approver="slack:U002", edited_body="New body")

    assert result.body == "New body"
    assert result.status == "approved"

    approvals = session.query(Approval).all()
    assert approvals[0].action == "edit"
    assert approvals[0].diff == "New body"


def test_reject(db_with_message) -> None:
    session, Session, msg_id = db_with_message

    with patch("src.agents.approval.SessionLocal", return_value=session):
        result = reject(msg_id, approver="slack:U003", reason="Not relevant")

    assert result.status == "rejected"

    approvals = session.query(Approval).all()
    assert approvals[0].action == "reject"
    assert approvals[0].reason == "Not relevant"


def test_approve_non_pending_raises(db_with_message) -> None:
    session, Session, msg_id = db_with_message

    msg = session.get(Message, msg_id)
    msg.status = "sent"
    session.commit()

    with patch("src.agents.approval.SessionLocal", return_value=session):
        with pytest.raises(ApprovalError, match="not pending_approval"):
            approve(msg_id, approver="slack:U004")


def test_mark_sent(db_with_message) -> None:
    session, Session, msg_id = db_with_message

    msg = session.get(Message, msg_id)
    msg.status = "approved"
    session.commit()

    with patch("src.agents.approval.SessionLocal", return_value=session):
        mark_sent(msg_id)

    verify = Session()
    msg = verify.get(Message, msg_id)
    assert msg.status == "sent"
    assert msg.sent_at is not None
    verify.close()
