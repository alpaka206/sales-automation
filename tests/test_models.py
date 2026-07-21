"""Tests for DB models — insert, defaults, and unique constraints."""

from __future__ import annotations

import pytest
from sqlalchemy import exc
from sqlalchemy.orm import Session

from src.db.models import Approval, Contact, Conversation, Event, Message


def test_contact_insert_and_defaults(db_session: Session) -> None:
    c = Contact(email="foo@bar.com", normalized_email="foo@bar.com", full_name="Foo Bar")
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)

    assert c.id is not None
    assert c.created_at is not None
    assert c.updated_at is not None


def test_contact_hubspot_id_unique(db_session: Session) -> None:
    c1 = Contact(
        hubspot_contact_id="hs-1",
        normalized_email="a@b.com",
        full_name="A",
    )
    c2 = Contact(
        hubspot_contact_id="hs-1",
        normalized_email="b@c.com",
        full_name="B",
    )
    db_session.add(c1)
    db_session.commit()
    db_session.add(c2)
    with pytest.raises(exc.IntegrityError):
        db_session.commit()


def test_message_and_approval_chain(db_session: Session) -> None:
    contact = Contact(normalized_email="x@y.com", full_name="X")
    db_session.add(contact)
    db_session.flush()

    conv = Conversation(contact_id=contact.id)
    db_session.add(conv)
    db_session.flush()

    msg = Message(conversation_id=conv.id, direction="outgoing", body="Hello")
    db_session.add(msg)
    db_session.flush()

    appr = Approval(message_id=msg.id, approver="slack:U123", action="approve")
    db_session.add(appr)
    db_session.commit()

    assert msg.status == "pending_approval"
    assert msg.replied is False
    assert len(msg.approvals) == 1


def test_event_insert(db_session: Session) -> None:
    e = Event(kind="llm_call", payload={"model": "gemini", "tokens": 100})
    db_session.add(e)
    db_session.commit()
    db_session.refresh(e)

    assert e.payload["tokens"] == 100
