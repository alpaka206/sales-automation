"""Slack integration for approval cards."""

from __future__ import annotations

import logging

import httpx

from ..common.config import settings

logger = logging.getLogger(__name__)


class SlackNotConfigured(RuntimeError):
    pass


def _approval_url(message_id: int) -> str:
    """Build the operator-facing approval URL for the local web UI."""
    base = settings.PUBLIC_BASE_URL.rstrip("/") if settings.PUBLIC_BASE_URL else f"http://{settings.APP_HOST}:{settings.APP_PORT}"
    return f"{base}/messages/{int(message_id)}"


def _escape_mrkdwn(text: str) -> str:
    """Escape user content to prevent Slack mrkdwn injection."""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def post_approval_card(
    message_id: int,
    subject: str,
    body_snippet: str,
    score: int | None,
    category: str,
    channel_type: str,
) -> None:
    """Post an approval card to the configured Slack channel.

    Note: this card is informational — the action buttons are removed because the app
    does not run a Slack Interactivity endpoint with signing-secret verification. The
    operator approves via the web UI link in the card (localhost-trusted) or via the
    /approve API with a per-message HMAC token.
    """
    if not settings.SLACK_BOT_TOKEN or not settings.SLACK_APPROVAL_CHANNEL_ID:
        raise SlackNotConfigured("SLACK_BOT_TOKEN or SLACK_APPROVAL_CHANNEL_ID not set.")

    url = _approval_url(message_id)
    subj_safe = _escape_mrkdwn(subject)
    body_safe = _escape_mrkdwn(body_snippet[:500])
    cat_safe = _escape_mrkdwn(category)

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Approval #{message_id}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Category:* {cat_safe}"},
                {"type": "mrkdwn", "text": f"*Score:* {score or 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Channel:* {channel_type}"},
                {"type": "mrkdwn", "text": f"*Subject:* {subj_safe}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```{body_safe}```"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"<{url}|Open in web UI to approve →>"},
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
