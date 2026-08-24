"""Tests for approval flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.approval import approve, reject, mark_sent, ApprovalError
from src.db.models import Approval, Contact, Conversation, Message


@pytest.fixture()
def db_with_message(db_session, db_session_factory):
    contact = Contact(normalized_email="a@b.com", full_name="Test")
    db_session.add(contact)
    db_session.flush()

    conv = Conversation(contact_id=contact.id)
    db_session.add(conv)
    db_session.flush()

    msg = Message(
        conversation_id=conv.id,
        direction="outgoing",
        body="Draft body",
        status="pending_approval",
    )
    db_session.add(msg)
    db_session.commit()

    yield db_session, db_session_factory, msg.id


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


def test_approve_freezes_subject_and_signature_in_same_commit(db_with_message) -> None:
    session, _Session, msg_id = db_with_message

    with patch("src.agents.approval.SessionLocal", return_value=session):
        result = approve(
            msg_id,
            approver="web:user",
            edited_body="Final body",
            edited_subject="Final subject",
            signature_key="none",
        )

    assert result.status == "approved"
    assert result.body == "Final body"
    assert result.subject == "Final subject"
    assert result.signature_key == "none"


def test_foreign_reply_requires_reviewed_translation(db_with_message) -> None:
    session, _Session, msg_id = db_with_message
    msg = session.get(Message, msg_id)
    msg.language = "ko"
    msg.target_language = "en"
    msg.body = "안녕하세요. 문의 주셔서 감사합니다."
    session.commit()

    with patch("src.agents.approval.SessionLocal", return_value=session):
        with pytest.raises(ApprovalError, match="번역하기"):
            approve(msg_id, approver="web:user")

    session.expire_all()
    assert session.get(Message, msg_id).status == "pending_approval"


def test_reviewed_foreign_translation_can_be_approved(db_with_message) -> None:
    session, _Session, msg_id = db_with_message
    msg = session.get(Message, msg_id)
    msg.language = "en"
    msg.target_language = "en"
    msg.body = "Hello. Thank you for your inquiry."
    session.commit()

    with patch("src.agents.approval.SessionLocal", return_value=session):
        result = approve(msg_id, approver="web:user")

    assert result.status == "approved"


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
