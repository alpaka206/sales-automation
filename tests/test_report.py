"""Tests for report agent."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.agents.report import ReportAgent
from src.db.models import Contact, Conversation, Message


@pytest.fixture()
def seeded_db(db_session):
    contact = Contact(normalized_email="a@b.com", full_name="A B")
    db_session.add(contact)
    db_session.flush()

    conv = Conversation(contact_id=contact.id)
    db_session.add(conv)
    db_session.flush()

    db_session.add(Message(
        conversation_id=conv.id, direction="outgoing", body="Hi",
        status="sent", sent_at=datetime.now(timezone.utc), replied=False,
    ))
    db_session.add(Message(
        conversation_id=conv.id, direction="outgoing", body="Follow",
        status="pending_approval", replied=False,
    ))
    db_session.add(Message(
        conversation_id=conv.id, direction="inbound", body="Reply",
        status="received", replied=True,
    ))

    db_session.commit()
    return db_session


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
    assert "## Summary" in report


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


def test_report_is_saved_without_an_external_send(seeded_db) -> None:
    session = seeded_db

    llm = MagicMock()
    llm.complete.return_value = "Narrative."

    with (
        patch("src.agents.report.SessionLocal", return_value=session),
        patch.object(ReportAgent, "_save_report") as save_report,
    ):
        agent = ReportAgent(llm=llm)
        report = agent.generate("daily")

    assert "# Daily Report" in report
    save_report.assert_called_once_with(report, "daily")
