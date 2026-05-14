"""Tests for reply check and follow-up drafting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.agents.reply_check import FollowupDraft, run
from src.db.models import Contact, Conversation, Message, Prospect


@pytest.fixture()
def db_setup(db_session, db_session_factory):
    contact = Contact(normalized_email="test@co.kr", full_name="Test User", company="TestCo")
    db_session.add(contact)
    db_session.flush()

    prospect = Prospect(
        source="manual_csv",
        full_name="Test User",
        normalized_email="test@co.kr",
        status="drafted",
        contact_id=contact.id,
        follow_up_count=0,
    )
    db_session.add(prospect)
    db_session.flush()

    conv = Conversation(
        contact_id=contact.id,
        prospect_id=prospect.id,
        topic="outbound_opening",
        stage="initial",
    )
    db_session.add(conv)
    db_session.flush()

    yield db_session, db_session_factory, contact, conv, prospect


def _mock_llm():
    llm = MagicMock()

    def side_effect(prompt_name, variables=None, schema=None, **kw):
        if "followup" in prompt_name:
            return FollowupDraft(subject="Re: Hi", body="Following up.", language="ko")
        return "ok"

    llm.complete = MagicMock(side_effect=side_effect)
    return llm


def test_no_followup_within_window(db_setup) -> None:
    session, Session, contact, conv, prospect = db_setup

    msg = Message(
        conversation_id=conv.id,
        direction="outbound",
        body="Hello",
        status="sent",
        sent_at=datetime.now(timezone.utc) - timedelta(days=1),
        replied=False,
    )
    session.add(msg)
    session.commit()

    with patch("src.agents.reply_check.SessionLocal", return_value=session):
        stats = run(llm=_mock_llm())

    assert stats["checked"] == 1
    assert stats["followup_drafted"] == 0


def test_followup_drafted_past_window(db_setup) -> None:
    session, Session, contact, conv, prospect = db_setup

    msg = Message(
        conversation_id=conv.id,
        direction="outbound",
        body="Hello",
        subject="Intro",
        status="sent",
        sent_at=datetime.now(timezone.utc) - timedelta(days=5),
        replied=False,
        channel="email",
        to_address="test@co.kr",
        language="ko",
    )
    session.add(msg)
    session.commit()

    with patch("src.agents.reply_check.SessionLocal", return_value=session):
        stats = run(llm=_mock_llm())

    assert stats["checked"] == 1
    assert stats["followup_drafted"] == 1

    all_msgs = session.query(Message).all()
    followups = [m for m in all_msgs if m.status == "pending_approval"]
    assert len(followups) == 1
    assert followups[0].subject == "Re: Hi"


def test_reply_detected(db_setup) -> None:
    session, Session, contact, conv, prospect = db_setup

    sent_time = datetime.now(timezone.utc) - timedelta(days=2)
    outgoing = Message(
        conversation_id=conv.id,
        direction="outbound",
        body="Hello",
        status="sent",
        sent_at=sent_time,
        replied=False,
    )
    session.add(outgoing)
    session.flush()
    outgoing_id = outgoing.id

    incoming = Message(
        conversation_id=conv.id,
        direction="inbound",
        body="Thanks, interested!",
        status="received",
        created_at=sent_time + timedelta(hours=12),
    )
    session.add(incoming)
    session.commit()

    with patch("src.agents.reply_check.SessionLocal", return_value=session):
        stats = run(llm=_mock_llm())

    assert stats["replied"] == 1
    assert stats["followup_drafted"] == 0

    verify = Session()
    msg = verify.get(Message, outgoing_id)
    assert msg.replied is True
    verify.close()
