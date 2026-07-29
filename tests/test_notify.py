"""Tests for the approval notification helper (Slack-only)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.agents import _notify
from src.agents._notify import (
    notify_approval,
    notify_approval_once,
    retry_pending_approval_notifications,
)
from src.common.config import settings
from src.db.models import Contact, Conversation, Message
from src.integrations.slack import SlackNotConfigured

_CALL_KWARGS = dict(
    message_id=42,
    subject="Hello",
    body_snippet="Test body",
    score=75,
    category="inquiry",
)


@patch("src.agents._notify.slack")
def test_sends_to_slack_when_configured(mock_slack) -> None:
    notify_approval(**_CALL_KWARGS)

    mock_slack.post_approval_card.assert_called_once_with(
        42, "Hello", "Test body", 75, "inquiry",
        title=None, inquiry=None, contact_name=None,
        contact_company=None, contact_email=None,
    )


@patch("src.agents._notify.slack")
def test_does_not_raise_when_slack_not_configured(mock_slack) -> None:
    mock_slack.post_approval_card.side_effect = SlackNotConfigured("no token")

    # Should swallow the error, not raise.
    notify_approval(**_CALL_KWARGS)

    mock_slack.post_approval_card.assert_called_once()


@patch("src.agents._notify.slack")
def test_does_not_raise_when_slack_errors(mock_slack) -> None:
    mock_slack.post_approval_card.side_effect = RuntimeError("network error")

    notify_approval(**_CALL_KWARGS)

    mock_slack.post_approval_card.assert_called_once()


@patch("src.agents._notify.notify_approval")
def test_once_respects_disabled_setting(mock_notify) -> None:
    with patch.object(settings, "APPROVAL_CHANNEL", "none"):
        notify_approval_once(**_CALL_KWARGS)
    mock_notify.assert_not_called()


@patch("src.agents._notify.notify_approval")
@patch("src.agents._notify._claim_slack_notification", return_value=False)
def test_once_skips_already_notified(_claim, mock_notify) -> None:
    with (
        patch.object(settings, "SLACK_ENABLED", True),
        patch.object(settings, "APPROVAL_CHANNEL", "slack"),
    ):
        notify_approval_once(**_CALL_KWARGS)
    mock_notify.assert_not_called()


@patch("src.agents._notify.notify_approval")
@patch("src.agents._notify._claim_slack_notification", return_value=True)
def test_once_sends_after_atomic_claim(_claim, mock_notify) -> None:
    with (
        patch.object(settings, "SLACK_ENABLED", True),
        patch.object(settings, "APPROVAL_CHANNEL", "slack"),
    ):
        notify_approval_once(**_CALL_KWARGS)
    mock_notify.assert_called_once_with(**_CALL_KWARGS)


@patch("src.agents._notify.notify_approval", return_value=True)
def test_failed_slack_attempt_is_retried_after_delay(
    mock_notify, db_session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(_notify, "SessionLocal", db_session_factory)
    with db_session_factory() as session:
        contact = Contact(normalized_email="retry@example.com", full_name="Retry Buyer")
        session.add(contact)
        session.flush()
        conversation = Conversation(contact_id=contact.id, inquiry_subject="inquiry")
        session.add(conversation)
        session.flush()
        message = Message(
            conversation_id=conversation.id,
            direction="outgoing",
            channel="email",
            subject="Reply",
            body="Draft",
            status="pending_approval",
            slack_notification_attempts=1,
            slack_notification_attempted_at=datetime.now(timezone.utc)
            - timedelta(seconds=_notify.SLACK_NOTIFICATION_RETRY_SECONDS + 1),
        )
        session.add(message)
        session.commit()
        message_id = message.id

    with (
        patch.object(settings, "SLACK_ENABLED", True),
        patch.object(settings, "APPROVAL_CHANNEL", "slack"),
    ):
        assert retry_pending_approval_notifications() == 1

    with db_session_factory() as session:
        stored = session.get(Message, message_id)
        assert stored.slack_notification_attempts == 2
        assert stored.slack_notified_at is not None
    mock_notify.assert_called_once()


@patch("src.agents._notify.notify_approval", return_value=True)
def test_ready_draft_missed_before_first_notify_is_recovered(
    mock_notify, db_session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(_notify, "SessionLocal", db_session_factory)
    with db_session_factory() as session:
        contact = Contact(normalized_email="missed@example.com", full_name="Missed")
        session.add(contact)
        session.flush()
        conversation = Conversation(contact_id=contact.id, inquiry_subject="inquiry")
        session.add(conversation)
        session.flush()
        session.add(
            Message(
                conversation_id=conversation.id,
                direction="outgoing",
                channel="email",
                subject="Reply",
                body="Draft",
                status="pending_approval",
                slack_notification_attempts=0,
                slack_notification_attempted_at=None,
            )
        )
        session.commit()

    with (
        patch.object(settings, "SLACK_ENABLED", True),
        patch.object(settings, "APPROVAL_CHANNEL", "slack"),
    ):
        assert retry_pending_approval_notifications() == 1
    mock_notify.assert_called_once()
