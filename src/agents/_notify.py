"""Notification helper — dispatches approval cards to Slack."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, update

from ..common.config import settings
from ..db.models import Contact, Conversation, Message
from ..db.session import SessionLocal
from ..integrations import slack
from ..integrations.slack import SlackNotConfigured

logger = logging.getLogger(__name__)
SLACK_NOTIFICATION_MAX_ATTEMPTS = 5
SLACK_NOTIFICATION_RETRY_SECONDS = 60


def _claim_slack_notification(message_id: int) -> bool:
    """Atomically reserve one immediate or due Slack notification attempt."""
    now = datetime.now(timezone.utc)
    retry_before = now - timedelta(seconds=SLACK_NOTIFICATION_RETRY_SECONDS)
    with SessionLocal() as session:
        result = session.execute(
            update(Message)
            .where(
                Message.id == message_id,
                Message.status == "pending_approval",
                Message.slack_notified_at.is_(None),
                Message.slack_notification_attempts < SLACK_NOTIFICATION_MAX_ATTEMPTS,
                or_(
                    Message.slack_notification_attempted_at.is_(None),
                    Message.slack_notification_attempted_at <= retry_before,
                ),
            )
            .values(
                slack_notification_attempted_at=now,
                slack_notification_attempts=Message.slack_notification_attempts + 1,
            )
        )
        session.commit()
        return result.rowcount == 1


def _mark_slack_notified(message_id: int) -> None:
    with SessionLocal() as session:
        session.execute(
            update(Message)
            .where(Message.id == message_id, Message.status == "pending_approval")
            .values(slack_notified_at=datetime.now(timezone.utc))
        )
        session.commit()


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
) -> bool:
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
        return True
    except SlackNotConfigured:
        logger.warning(
            "Slack is not configured. Approval card for message %d not sent.",
            message_id,
        )
        return False
    except Exception:
        logger.warning("Slack notification failed for message %d.", message_id, exc_info=True)
        return False


def notify_approval_once(*args, **kwargs) -> bool:
    """Notify only when enabled and only once for the pending reply row."""
    if not settings.SLACK_ENABLED or settings.APPROVAL_CHANNEL != "slack":
        logger.info("Slack approval notifications are disabled.")
        return False
    message_id = int(kwargs.get("message_id", args[0] if args else 0))
    if not message_id or not _claim_slack_notification(message_id):
        logger.info("Slack approval notification already handled for message %s.", message_id)
        return False
    if notify_approval(*args, **kwargs):
        _mark_slack_notified(message_id)
        return True
    return False


def retry_pending_approval_notifications(limit: int = 20) -> int:
    """Retry only Slack attempts that previously failed and whose delay elapsed."""
    if not settings.SLACK_ENABLED or settings.APPROVAL_CHANNEL != "slack":
        return 0
    retry_before = datetime.now(timezone.utc) - timedelta(
        seconds=SLACK_NOTIFICATION_RETRY_SECONDS
    )
    with SessionLocal() as session:
        rows = (
            session.query(Message, Conversation, Contact)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .join(Contact, Contact.id == Conversation.contact_id)
            .filter(
                Message.status == "pending_approval",
                Message.slack_notified_at.is_(None),
                Message.slack_notification_attempts < SLACK_NOTIFICATION_MAX_ATTEMPTS,
                or_(
                    Message.slack_notification_attempted_at.is_(None),
                    Message.slack_notification_attempted_at <= retry_before,
                ),
            )
            .order_by(Message.slack_notification_attempted_at)
            .limit(limit)
            .all()
        )
        payloads = []
        for message, conversation, contact in rows:
            inbound = (
                session.query(Message)
                .filter(
                    Message.conversation_id == conversation.id,
                    Message.direction == "inbound",
                )
                .order_by(Message.created_at.desc())
                .first()
            )
            payloads.append(
                {
                    "message_id": message.id,
                    "subject": message.subject or "",
                    "body_snippet": message.body,
                    "score": message.score_snapshot,
                    "category": conversation.topic or "inquiry",
                    "title": "새 인바운드 문의 — 회신 검토 요청",
                    "inquiry": inbound.body if inbound else None,
                    "contact_name": contact.full_name,
                    "contact_company": contact.company,
                    "contact_email": contact.email,
                }
            )

    for payload in payloads:
        notify_approval_once(**payload)
    return len(payloads)
