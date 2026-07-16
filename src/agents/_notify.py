"""Notification helper — dispatches approval cards to Slack."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import update

from ..common.config import settings
from ..db.models import Message
from ..db.session import SessionLocal
from ..integrations import slack
from ..integrations.slack import SlackNotConfigured

logger = logging.getLogger(__name__)


def _claim_slack_notification(message_id: int) -> bool:
    """Atomically reserve the only Slack approval notification for a message."""
    with SessionLocal() as session:
        result = session.execute(
            update(Message)
            .where(
                Message.id == message_id,
                Message.status == "pending_approval",
                Message.slack_notified_at.is_(None),
            )
            .values(slack_notified_at=datetime.now(timezone.utc))
        )
        session.commit()
        return result.rowcount == 1


def notify_approval(
    message_id: int,
    subject: str,
    body_snippet: str,
    score: int | None,
    category: str,
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
    inbound agent).
    """
    try:
        slack.post_approval_card(
            message_id, subject, body_snippet, score, category,
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


def notify_approval_once(*args, **kwargs) -> None:
    """Notify only when enabled and only once for the pending reply row."""
    if not settings.SLACK_ENABLED or settings.APPROVAL_CHANNEL != "slack":
        logger.info("Slack approval notifications are disabled.")
        return
    message_id = int(kwargs.get("message_id", args[0] if args else 0))
    if not message_id or not _claim_slack_notification(message_id):
        logger.info("Slack approval notification already handled for message %s.", message_id)
        return
    notify_approval(*args, **kwargs)
