"""Notification helper — dispatches approval cards to Slack."""

from __future__ import annotations

import logging

from ..integrations import slack
from ..integrations.slack import SlackNotConfigured

logger = logging.getLogger(__name__)


def notify_approval(
    message_id: int,
    subject: str,
    body_snippet: str,
    score: int | None,
    category: str,
    channel: str,
) -> None:
    """Post an approval card to Slack; log a warning if Slack isn't configured."""
    try:
        slack.post_approval_card(message_id, subject, body_snippet, score, category, channel)
    except SlackNotConfigured:
        logger.warning(
            "Slack is not configured. Approval card for message %d not sent.",
            message_id,
        )
    except Exception:
        logger.warning("Slack notification failed for message %d.", message_id, exc_info=True)
