"""Tests for report agent."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agents.report import ReportAgent
from src.db.base import Base
from src.db.models import Contact, Conversation, Message, Prospect


@pytest.fixture()
def seeded_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    contact = Contact(normalized_email="a@b.com", full_name="A B")
    session.add(contact)
    session.flush()

    conv = Conversation(contact_id=contact.id)
    session.add(conv)
    session.flush()

    session.add(Message(
        conversation_id=conv.id, direction="outbound", body="Hi",
        status="sent", sent_at=datetime.now(timezone.utc), replied=False,
    ))
    session.add(Message(
        conversation_id=conv.id, direction="outbound", body="Follow",
        status="pending_approval", replied=False,
    ))
    session.add(Message(
        conversation_id=conv.id, direction="inbound", body="Reply",
        status="received", replied=True,
    ))

    session.add(Prospect(
        source="manual_csv", full_name="P1", status="drafted",
        created_at=datetime.now(timezone.utc),
    ))
    session.add(Prospect(
        source="manual_csv", full_name="P2", status="skipped_lowscore",
        created_at=datetime.now(timezone.utc),
    ))

    session.commit()
    yield session
    session.close()


def test_daily_report(seeded_db) -> None:
    session = seeded_db

    llm = MagicMock()
    llm.complete.return_value = "Activity summary for today."

    with (
        patch("src.agents.report.SessionLocal", return_value=session),
        patch.object(ReportAgent, "_save_report"),
    ):
        agent = ReportAgent(llm=llm)
        report = agent.generate("daily")

    assert "# Daily Report" in report
    assert "## Summary" in report
    assert "Messages sent: **1**" in report
    assert "Pending approval: **1**" in report
    assert "manual_csv" in report


def test_weekly_report(seeded_db) -> None:
    session = seeded_db

    llm = MagicMock()
    llm.complete.return_value = "Weekly summary."

    with (
        patch("src.agents.report.SessionLocal", return_value=session),
        patch.object(ReportAgent, "_save_report"),
    ):
        agent = ReportAgent(llm=llm)
        report = agent.generate("weekly")

    assert "# Weekly Report" in report
    assert "## Prospects by Status" in report


def test_report_llm_fallback(seeded_db) -> None:
    session = seeded_db

    llm = MagicMock()
    llm.complete.side_effect = RuntimeError("LLM down")

    with (
        patch("src.agents.report.SessionLocal", return_value=session),
        patch.object(ReportAgent, "_save_report"),
    ):
        agent = ReportAgent(llm=llm)
        report = agent.generate("daily")

    assert "# Daily Report" in report
    assert "messages sent" in report
