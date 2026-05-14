"""Tests for DB models — insert, defaults, and unique constraints."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, exc
from sqlalchemy.orm import Session, sessionmaker

from src.db.base import Base
from src.db.models import Approval, Contact, Conversation, Event, Message, Prospect


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_contact_insert_and_defaults(db: Session) -> None:
    c = Contact(email="foo@bar.com", normalized_email="foo@bar.com", full_name="Foo Bar")
    db.add(c)
    db.commit()
    db.refresh(c)

    assert c.id is not None
    assert c.whatsapp_opt_in is False
    assert c.created_at is not None
    assert c.updated_at is not None


def test_contact_hubspot_id_unique(db: Session) -> None:
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
    db.add(c1)
    db.commit()
    db.add(c2)
    with pytest.raises(exc.IntegrityError):
        db.commit()


def test_prospect_defaults(db: Session) -> None:
    p = Prospect(source="youtube", full_name="Test User")
    db.add(p)
    db.commit()
    db.refresh(p)

    assert p.status == "candidate"
    assert p.follow_up_count == 0


def test_message_and_approval_chain(db: Session) -> None:
    contact = Contact(normalized_email="x@y.com", full_name="X")
    db.add(contact)
    db.flush()

    conv = Conversation(contact_id=contact.id)
    db.add(conv)
    db.flush()

    msg = Message(conversation_id=conv.id, direction="outbound", body="Hello")
    db.add(msg)
    db.flush()

    appr = Approval(message_id=msg.id, approver="slack:U123", action="approve")
    db.add(appr)
    db.commit()

    assert msg.status == "pending_approval"
    assert msg.replied is False
    assert len(msg.approvals) == 1


def test_event_insert(db: Session) -> None:
    e = Event(kind="llm_call", payload={"model": "claude", "tokens": 100})
    db.add(e)
    db.commit()
    db.refresh(e)

    assert e.payload["tokens"] == 100
