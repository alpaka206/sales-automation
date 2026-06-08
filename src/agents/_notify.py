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
    *,
    title: str | None = None,
    inquiry: str | None = None,
    contact_name: str | None = None,
    contact_company: str | None = None,
    contact_email: str | None = None,
) -> None:
    """Post an approval card to Slack; log a warning if Slack isn't configured.

    The optional ``title`` / ``inquiry`` / ``contact_*`` fields enrich the card so the
    operator sees who asked, what they asked, and what reply will go out (used by the
    inbound agent). Outbound/follow-up callers can omit them.
    """
    try:
        slack.post_approval_card(
            message_id, subject, body_snippet, score, category, channel,
            title=title,
            inquiry=inquiry,
            contact_name=contact_name,
            contact_company=contact_company,
            contact_email=contact_email,
        )
    except SlackNotConfigured:
        logger.warning(
            "Slack is not configured. Approval card for message %d not sent.",
            message_id,
        )
    except Exception:
        logger.warning("Slack notification failed for message %d.", message_id, exc_info=True)
