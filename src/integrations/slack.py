"""Slack integration for approval cards."""

from __future__ import annotations

import logging

import httpx

from ..common.config import settings

logger = logging.getLogger(__name__)


class SlackNotConfigured(RuntimeError):
    pass


def post_approval_card(
    message_id: int,
    subject: str,
    body_snippet: str,
    score: int | None,
    category: str,
    channel_type: str,
) -> None:
    """Post an approval card to the configured Slack channel."""
    if not settings.SLACK_BOT_TOKEN or not settings.SLACK_APPROVAL_CHANNEL_ID:
        raise SlackNotConfigured("SLACK_BOT_TOKEN or SLACK_APPROVAL_CHANNEL_ID not set.")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Approval #{message_id}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Category:* {category}"},
                {"type": "mrkdwn", "text": f"*Score:* {score or 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Channel:* {channel_type}"},
                {"type": "mrkdwn", "text": f"*Subject:* {subject}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```{body_snippet[:500]}```"},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": f"approve_{message_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": f"reject_{message_id}",
                },
            ],
        },
    ]

    with httpx.Client(timeout=10) as client:
        r = client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"},
            json={
                "channel": settings.SLACK_APPROVAL_CHANNEL_ID,
                "blocks": blocks,
                "text": f"Approval needed for message #{message_id}",
            },
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            logger.error("Slack API error: %s", data.get("error"))

    logger.info("Posted approval card for message %d to Slack.", message_id)


def post_message(channel: str, text: str) -> None:
    """Post a plain mrkdwn text message to a Slack channel."""
    if not settings.SLACK_BOT_TOKEN:
        raise SlackNotConfigured("SLACK_BOT_TOKEN not set.")

    with httpx.Client(timeout=10) as client:
        r = client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"},
            json={"channel": channel, "text": text},
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            logger.error("Slack API error: %s", data.get("error"))

    logger.info("Posted message to Slack channel %s.", channel)
