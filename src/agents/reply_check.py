"""Reply check job - detects replies and queues follow-ups."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from ..common.config import settings
from ..db.models import Conversation, Message, Prospect
from ..db.session import SessionLocal
from ..integrations.gmail_imap import IMAPClient, IMAPNotConfigured
from ..llm.client import LLMClient
from ._notify import notify_approval
from .outbound.status import ProspectStatus, transition, InvalidStatusTransition

logger = logging.getLogger(__name__)


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


class FollowupDraft(BaseModel):
    subject: str
    body: str
    language: str = "ko"


def run(llm: LLMClient | None = None) -> dict:
    """Check for replies on sent messages and queue follow-ups. Returns stats."""
    llm = llm or LLMClient()
    session = SessionLocal()
    stats = {"checked": 0, "replied": 0, "followup_drafted": 0}

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        messages = (
            session.query(Message)
            .filter(
                Message.status == "sent",
                Message.replied.is_(False),
                Message.sent_at > cutoff,
            )
            .all()
        )

        for msg in messages:
            stats["checked"] += 1
            conv = session.get(Conversation, msg.conversation_id)
            if not conv:
                continue

            if _check_reply(session, msg, conv):
                stats["replied"] += 1
                continue

            if _should_followup(session, msg, conv):
                _draft_followup(session, msg, conv, llm)
                stats["followup_drafted"] += 1

        session.commit()
    finally:
        session.close()

    logger.info("Reply check complete: %s", stats)
    return stats


def _check_reply(session, msg: Message, conv: Conversation) -> bool:
    """Check if we received a reply via DB records or IMAP."""
    incoming = (
        session.query(Message)
        .filter(
            Message.conversation_id == conv.id,
            Message.direction == "inbound",
            Message.created_at > msg.sent_at,
        )
        .first()
    )

    if incoming:
        return _mark_replied(session, msg, conv, incoming.created_at)

    if msg.smtp_message_id and settings.EMAIL_PROVIDER == "smtp":
        if _check_imap_reply(session, msg, conv):
            return True

    return False


def _check_imap_reply(session, msg: Message, conv: Conversation) -> bool:
    """Check Gmail IMAP for replies to a specific outbound message."""
    try:
        client = IMAPClient()
    except IMAPNotConfigured:
        return False

    if not msg.sent_at:
        return False

    replies = client.fetch_replies(since_dt=_as_utc(msg.sent_at))

    for reply in replies:
        if _matches_reply(msg, reply):
            incoming = Message(
                conversation_id=conv.id,
                direction="inbound",
                channel="email",
                from_address=reply["from_addr"],
                to_address=settings.SMTP_FROM_EMAIL,
                subject=reply["subject"],
                body=reply["body_snippet"],
                in_reply_to=reply.get("in_reply_to", ""),
                status="received",
            )
            session.add(incoming)
            session.flush()
            return _mark_replied(session, msg, conv, incoming.created_at)

    return False


def _matches_reply(outbound: Message, imap_msg: dict) -> bool:
    """Check if an IMAP message is a reply to our outbound message."""
    if outbound.smtp_message_id:
        in_reply_to = imap_msg.get("in_reply_to", "")
        if outbound.smtp_message_id in in_reply_to:
            return True
        references = imap_msg.get("references", "")
        if outbound.smtp_message_id in references:
            return True

    if outbound.to_address and imap_msg.get("from_addr"):
        if outbound.to_address.lower() == imap_msg["from_addr"].lower():
            received_at = imap_msg.get("received_at")
            if received_at and outbound.sent_at:
                if received_at > _as_utc(outbound.sent_at):
                    return True

    return False


def _mark_replied(session, msg: Message, conv: Conversation, reply_time: datetime) -> bool:
    """Mark a message as replied and update conversation state."""
    msg.replied = True
    conv.last_incoming_at = reply_time
    conv.stage = "replied"
    if conv.prospect_id:
        try:
            transition(session, conv.prospect_id, ProspectStatus.REPLIED, reason="reply_detected")
        except (InvalidStatusTransition, ValueError):
            pass
    return True


def _should_followup(session, msg: Message, conv: Conversation) -> bool:
    """Check if enough time has passed and we haven't exceeded follow-up limit."""
    if not msg.sent_at:
        return False

    days_since = (datetime.now(timezone.utc) - _as_utc(msg.sent_at)).days
    if days_since < settings.FOLLOWUP_AFTER_DAYS:
        return False

    prospect = session.get(Prospect, conv.prospect_id) if conv.prospect_id else None
    followup_count = prospect.follow_up_count if prospect else 0
    return followup_count < settings.MAX_FOLLOWUPS_PER_PROSPECT


def _draft_followup(session, original: Message, conv: Conversation, llm: LLMClient) -> None:
    """Draft a follow-up message."""
    days_since = (datetime.now(timezone.utc) - _as_utc(original.sent_at)).days if original.sent_at else 0

    prospect = session.get(Prospect, conv.prospect_id) if conv.prospect_id else None
    followup_num = (prospect.follow_up_count if prospect else 0) + 1

    contact_name = "there"
    company = ""
    if conv.contact:
        contact_name = conv.contact.full_name
        company = conv.contact.company or ""

    draft = llm.complete(
        "outbound/followup",
        {
            "full_name": contact_name,
            "company": company,
            "previous_subject": original.subject or "",
            "days_since": str(days_since),
            "followup_number": str(followup_num),
            "language": original.language or "ko",
        },
        schema=FollowupDraft,
    )

    status = "approved" if settings.FOLLOWUP_AUTO_SEND else "pending_approval"

    followup = Message(
        conversation_id=conv.id,
        direction="outbound",
        channel=original.channel,
        to_address=original.to_address,
        subject=draft.subject,
        body=draft.body,
        language=draft.language,
        status=status,
        draft_provider=settings.LLM_PROVIDER,
    )
    session.add(followup)
    session.flush()

    try:
        notify_approval(
            message_id=followup.id,
            subject=draft.subject,
            body_snippet=draft.body,
            score=None,
            category="followup",
            channel=original.channel or "email",
        )
    except Exception:
        logger.warning("Approval notification failed for follow-up %d.", followup.id, exc_info=True)

    if prospect:
        prospect.follow_up_count = followup_num

    logger.info("Drafted follow-up #%d for conversation %d.", followup_num, conv.id)
