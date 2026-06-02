"""Tests for report agent."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.agents.report import ReportAgent
from src.db.models import Contact, Conversation, Message, Prospect


@pytest.fixture()
def seeded_db(db_session):
    contact = Contact(normalized_email="a@b.com", full_name="A B")
    db_session.add(contact)
    db_session.flush()

    conv = Conversation(contact_id=contact.id)
    db_session.add(conv)
    db_session.flush()

    db_session.add(Message(
        conversation_id=conv.id, direction="outbound", body="Hi",
        status="sent", sent_at=datetime.now(timezone.utc), replied=False,
    ))
    db_session.add(Message(
        conversation_id=conv.id, direction="outbound", body="Follow",
        status="pending_approval", replied=False,
    ))
    db_session.add(Message(
        conversation_id=conv.id, direction="inbound", body="Reply",
        status="received", replied=True,
    ))

    db_session.add(Prospect(
        source="manual_csv", full_name="P1", status="drafted",
        created_at=datetime.now(timezone.utc),
    ))
    db_session.add(Prospect(
        source="manual_csv", full_name="P2", status="skipped_lowscore",
        created_at=datetime.now(timezone.utc),
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
        patch.object(ReportAgent, "_distribute"),
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
        patch.object(ReportAgent, "_distribute"),
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
        patch.object(ReportAgent, "_distribute"),
    ):
        agent = ReportAgent(llm=llm)
        report = agent.generate("daily")

    assert "# Daily Report" in report
    assert "messages sent" in report


@patch("src.agents.report.smtplib")
@patch("src.agents.report.slack")
def test_distribute_calls_all_channels(mock_slack, mock_smtp, seeded_db) -> None:
    session = seeded_db

    llm = MagicMock()
    llm.complete.return_value = "Narrative."

    with (
        patch("src.agents.report.SessionLocal", return_value=session),
        patch.object(ReportAgent, "_save_report"),
        patch("src.agents.report.settings") as mock_settings,
    ):
        mock_settings.REPORT_SLACK_CHANNEL_ID = "C-REPORT"
        mock_settings.SLACK_APPROVAL_CHANNEL_ID = ""
        mock_settings.REPORT_EMAIL_TO = "boss@co.com,team@co.com"
        mock_settings.SMTP_USERNAME = "user"
        mock_settings.SMTP_PASSWORD = "pass"
        mock_settings.SMTP_HOST = "smtp.test.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_FROM_NAME = "Bot"
        mock_settings.SMTP_FROM_EMAIL = "bot@co.com"

        agent = ReportAgent(llm=llm)
        agent.generate("daily")

    mock_slack.post_message.assert_called_once()
    assert mock_slack.post_message.call_args[0][0] == "C-REPORT"

    mock_smtp.SMTP.assert_called_once()


@patch("src.agents.report.slack")
def test_distribute_survives_all_failures(mock_slack, seeded_db) -> None:
    session = seeded_db

    mock_slack.post_message.side_effect = RuntimeError("slack down")

    llm = MagicMock()
    llm.complete.return_value = "Narrative."

    with (
        patch("src.agents.report.SessionLocal", return_value=session),
        patch.object(ReportAgent, "_save_report"),
        patch("src.agents.report.settings") as mock_settings,
    ):
        mock_settings.REPORT_SLACK_CHANNEL_ID = "C-REPORT"
        mock_settings.SLACK_APPROVAL_CHANNEL_ID = ""
        mock_settings.REPORT_EMAIL_TO = ""

        agent = ReportAgent(llm=llm)
        report = agent.generate("daily")

    assert "# Daily Report" in report
