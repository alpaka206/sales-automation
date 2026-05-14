"""Microsoft Teams integration for approval cards."""

from __future__ import annotations

import logging

import httpx

from ..common.config import settings

logger = logging.getLogger(__name__)


class TeamsNotConfigured(RuntimeError):
    pass


def post_approval_card(
    message_id: int,
    subject: str,
    body_snippet: str,
    score: int | None,
    category: str,
    channel_type: str,
) -> None:
    """Post an approval card to Teams via incoming webhook."""
    if not settings.TEAMS_WEBHOOK_URL:
        raise TeamsNotConfigured("TEAMS_WEBHOOK_URL not set.")

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
    }

    with httpx.Client(timeout=10) as client:
        r = client.post(settings.TEAMS_WEBHOOK_URL, json=card)
        r.raise_for_status()

    logger.info("Posted approval card for message %d to Teams.", message_id)
