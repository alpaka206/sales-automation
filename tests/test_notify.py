"""Tests for the approval notification helper (Slack-only)."""

from __future__ import annotations

from unittest.mock import patch


from src.agents._notify import notify_approval
from src.integrations.slack import SlackNotConfigured

_CALL_KWARGS = dict(
    message_id=42,
    subject="Hello",
    body_snippet="Test body",
    score=75,
    category="inquiry",
    channel="email",
)


@patch("src.agents._notify.slack")
def test_sends_to_slack_when_configured(mock_slack) -> None:
    notify_approval(**_CALL_KWARGS)

    mock_slack.post_approval_card.assert_called_once_with(
        42, "Hello", "Test body", 75, "inquiry", "email",
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
