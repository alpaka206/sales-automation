"""Microsoft Teams integration for approval cards."""

from __future__ import annotations

import logging

import httpx

from ..common.config import settings

logger = logging.getLogger(__name__)


class TeamsNotConfigured(RuntimeError):
    pass


def _approval_url(message_id: int) -> str:
    base = settings.PUBLIC_BASE_URL.rstrip("/") if settings.PUBLIC_BASE_URL else f"http://{settings.APP_HOST}:{settings.APP_PORT}"
    return f"{base}/messages/{int(message_id)}"


def post_approval_card(
    message_id: int,
    subject: str,
    body_snippet: str,
    score: int | None,
    category: str,
    channel_type: str,
) -> None:
    """Post an approval card to Teams via incoming webhook.

    Includes a deep link to the local web UI for the operator to approve.
    """
    if not settings.TEAMS_WEBHOOK_URL:
        raise TeamsNotConfigured("TEAMS_WEBHOOK_URL not set.")

    url = _approval_url(message_id)
    card = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": f"Approval #{message_id}",
        "themeColor": "0078D7",
        "title": f"Approval #{message_id}: {subject}",
        "sections": [
            {
                "facts": [
                    {"name": "Category", "value": category},
                    {"name": "Score", "value": str(score or "N/A")},
                    {"name": "Channel", "value": channel_type},
                ],
                "text": body_snippet[:500],
            }
        ],
        "potentialAction": [
            {
                "@type": "OpenUri",
                "name": "Open in web UI",
                "targets": [{"os": "default", "uri": url}],
            }
        ],
    }

    with httpx.Client(timeout=10) as client:
        r = client.post(settings.TEAMS_WEBHOOK_URL, json=card)
        r.raise_for_status()

    logger.info("Posted approval card for message %d to Teams.", message_id)


def post_message(text: str) -> None:
    """Post a plain text message to Teams via incoming webhook."""
    if not settings.TEAMS_WEBHOOK_URL:
        raise TeamsNotConfigured("TEAMS_WEBHOOK_URL not set.")

    card = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": "Report",
        "text": text,
    }

    with httpx.Client(timeout=10) as client:
        r = client.post(settings.TEAMS_WEBHOOK_URL, json=card)
        r.raise_for_status()

    logger.info("Posted message to Teams.")
