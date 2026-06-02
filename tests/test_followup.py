"""Tests for follow-up queue behavior."""

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


def test_followup_after_7_days(db_setup) -> None:
    session, Session, contact, conv, prospect = db_setup

    msg = Message(
        conversation_id=conv.id,
        direction="outbound",
        body="Hello",
        subject="Intro",
        status="sent",
        sent_at=datetime.now(timezone.utc) - timedelta(days=8),
        replied=False,
        channel="email",
        to_address="test@co.kr",
        language="ko",
    )
    session.add(msg)
    session.commit()

    with (
        patch("src.agents.reply_check.SessionLocal", return_value=session),
        patch("src.agents.reply_check.settings") as mock_settings,
    ):
        mock_settings.FOLLOWUP_AFTER_DAYS = 7
        mock_settings.FOLLOWUP_AUTO_SEND = False
        mock_settings.MAX_FOLLOWUPS_PER_PROSPECT = 2
        mock_settings.LLM_PROVIDER = "gemini_vertex"
        mock_settings.EMAIL_PROVIDER = "hubspot"
        stats = run(llm=_mock_llm())

    assert stats["followup_drafted"] == 1

    followups = [m for m in session.query(Message).all() if m.status == "pending_approval"]
    assert len(followups) == 1


def test_no_followup_before_7_days(db_setup) -> None:
    session, Session, contact, conv, prospect = db_setup

    msg = Message(
        conversation_id=conv.id,
        direction="outbound",
        body="Hello",
        status="sent",
        sent_at=datetime.now(timezone.utc) - timedelta(days=5),
        replied=False,
    )
    session.add(msg)
    session.commit()

    with (
        patch("src.agents.reply_check.SessionLocal", return_value=session),
        patch("src.agents.reply_check.settings") as mock_settings,
    ):
        mock_settings.FOLLOWUP_AFTER_DAYS = 7
        mock_settings.FOLLOWUP_AUTO_SEND = False
        mock_settings.MAX_FOLLOWUPS_PER_PROSPECT = 2
        mock_settings.LLM_PROVIDER = "gemini_vertex"
        mock_settings.EMAIL_PROVIDER = "hubspot"
        stats = run(llm=_mock_llm())

    assert stats["followup_drafted"] == 0


def test_followup_auto_send_true(db_setup) -> None:
    session, Session, contact, conv, prospect = db_setup

    msg = Message(
        conversation_id=conv.id,
        direction="outbound",
        body="Hello",
        subject="Intro",
        status="sent",
        sent_at=datetime.now(timezone.utc) - timedelta(days=8),
        replied=False,
        channel="email",
        to_address="test@co.kr",
        language="ko",
    )
    session.add(msg)
    session.commit()

    with (
        patch("src.agents.reply_check.SessionLocal", return_value=session),
        patch("src.agents.reply_check.settings") as mock_settings,
    ):
        mock_settings.FOLLOWUP_AFTER_DAYS = 7
        mock_settings.FOLLOWUP_AUTO_SEND = True
        mock_settings.MAX_FOLLOWUPS_PER_PROSPECT = 2
        mock_settings.LLM_PROVIDER = "gemini_vertex"
        mock_settings.EMAIL_PROVIDER = "hubspot"
        stats = run(llm=_mock_llm())

    assert stats["followup_drafted"] == 1

    followups = [m for m in session.query(Message).all() if m.status == "approved"]
    assert len(followups) == 1


def test_max_followups_reached(db_setup) -> None:
    session, Session, contact, conv, prospect = db_setup
    prospect.follow_up_count = 2

    msg = Message(
        conversation_id=conv.id,
        direction="outbound",
        body="Hello",
        status="sent",
        sent_at=datetime.now(timezone.utc) - timedelta(days=14),
        replied=False,
    )
    session.add(msg)
    session.commit()

    with (
        patch("src.agents.reply_check.SessionLocal", return_value=session),
        patch("src.agents.reply_check.settings") as mock_settings,
    ):
        mock_settings.FOLLOWUP_AFTER_DAYS = 7
        mock_settings.FOLLOWUP_AUTO_SEND = False
        mock_settings.MAX_FOLLOWUPS_PER_PROSPECT = 2
        mock_settings.LLM_PROVIDER = "gemini_vertex"
        mock_settings.EMAIL_PROVIDER = "hubspot"
        stats = run(llm=_mock_llm())

    assert stats["followup_drafted"] == 0


def test_replied_message_no_followup(db_setup) -> None:
    session, Session, contact, conv, prospect = db_setup

    msg = Message(
        conversation_id=conv.id,
        direction="outbound",
        body="Hello",
        status="sent",
        sent_at=datetime.now(timezone.utc) - timedelta(days=10),
        replied=True,
    )
    session.add(msg)
    session.commit()

    with (
        patch("src.agents.reply_check.SessionLocal", return_value=session),
        patch("src.agents.reply_check.settings") as mock_settings,
    ):
        mock_settings.FOLLOWUP_AFTER_DAYS = 7
        mock_settings.FOLLOWUP_AUTO_SEND = False
        mock_settings.MAX_FOLLOWUPS_PER_PROSPECT = 2
        mock_settings.LLM_PROVIDER = "gemini_vertex"
        mock_settings.EMAIL_PROVIDER = "hubspot"
        stats = run(llm=_mock_llm())

    assert stats["checked"] == 0
    assert stats["followup_drafted"] == 0
