"""Tests for the approval notification helper."""

from __future__ import annotations

from unittest.mock import patch


from src.agents._notify import notify_approval
from src.integrations.slack import SlackNotConfigured
from src.integrations.teams import TeamsNotConfigured

_CALL_KWARGS = dict(
    message_id=42,
    subject="Hello",
    body_snippet="Test body",
    score=75,
    category="inquiry",
    channel="email",
)


@patch("src.agents._notify.teams")
@patch("src.agents._notify.slack")
def test_sends_to_slack_when_configured(mock_slack, mock_teams) -> None:
    notify_approval(**_CALL_KWARGS)

    mock_slack.post_approval_card.assert_called_once_with(42, "Hello", "Test body", 75, "inquiry", "email")
    mock_teams.post_approval_card.assert_not_called()


@patch("src.agents._notify.teams")
@patch("src.agents._notify.slack")
def test_falls_back_to_teams_when_slack_not_configured(mock_slack, mock_teams) -> None:
    mock_slack.post_approval_card.side_effect = SlackNotConfigured("no token")

    notify_approval(**_CALL_KWARGS)

    mock_slack.post_approval_card.assert_called_once()
    mock_teams.post_approval_card.assert_called_once_with(42, "Hello", "Test body", 75, "inquiry", "email")


@patch("src.agents._notify.teams")
@patch("src.agents._notify.slack")
def test_falls_back_to_teams_when_slack_errors(mock_slack, mock_teams) -> None:
    mock_slack.post_approval_card.side_effect = RuntimeError("network error")

    notify_approval(**_CALL_KWARGS)

    mock_teams.post_approval_card.assert_called_once()


@patch("src.agents._notify.teams")
@patch("src.agents._notify.slack")
def test_logs_warning_when_neither_configured(mock_slack, mock_teams) -> None:
    mock_slack.post_approval_card.side_effect = SlackNotConfigured("no token")
    mock_teams.post_approval_card.side_effect = TeamsNotConfigured("no webhook")

    notify_approval(**_CALL_KWARGS)

    mock_slack.post_approval_card.assert_called_once()
    mock_teams.post_approval_card.assert_called_once()


@patch("src.agents._notify.teams")
@patch("src.agents._notify.slack")
def test_does_not_raise_when_both_fail(mock_slack, mock_teams) -> None:
    mock_slack.post_approval_card.side_effect = SlackNotConfigured("no token")
    mock_teams.post_approval_card.side_effect = RuntimeError("teams down")

    notify_approval(**_CALL_KWARGS)
