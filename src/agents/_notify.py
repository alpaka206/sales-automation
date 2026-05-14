"""Notification helper — dispatches approval cards to Slack or Teams."""

from __future__ import annotations

import logging

from ..integrations import slack, teams
from ..integrations.slack import SlackNotConfigured
from ..integrations.teams import TeamsNotConfigured

logger = logging.getLogger(__name__)


def notify_approval(
    message_id: int,
    subject: str,
    body_snippet: str,
    score: int | None,
    category: str,
    channel: str,
) -> None:
    """Try Slack, then Teams, then log a warning."""
    try:
        slack.post_approval_card(message_id, subject, body_snippet, score, category, channel)
        return
    except SlackNotConfigured:
        logger.debug("Slack not configured, trying Teams.")
    except Exception:
        logger.warning("Slack notification failed for message %d.", message_id, exc_info=True)

    try:
        teams.post_approval_card(message_id, subject, body_snippet, score, category, channel)
        return
    except TeamsNotConfigured:
        logger.warning(
            "Neither Slack nor Teams is configured. Approval card for message %d not sent.",
            message_id,
        )
    except Exception:
        logger.warning("Teams notification failed for message %d.", message_id, exc_info=True)
