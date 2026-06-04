"""Approval handler - processes approve/edit/reject actions on messages."""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone

from ..common.config import settings
from ..db.models import Approval, Conversation, Message
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)


class ApprovalError(RuntimeError):
    pass


def make_approval_token(message_id: int) -> str:
    """Generate an HMAC-SHA256 token binding a message_id to the install secret.

    The token is short (16 bytes hex = 32 chars) and stable for the lifetime of the
    message — we don't need rotation since approve() rejects non-pending messages.
    """
    secret = settings.INTERNAL_API_TOKEN
    if not secret:
        # No secret configured → no token to verify against. Caller must enforce
        # APPROVAL_REQUIRE_TOKEN=False explicitly or set INTERNAL_API_TOKEN.
        raise ApprovalError("INTERNAL_API_TOKEN is not set; cannot mint approval tokens.")
    payload = f"approval:{int(message_id)}".encode()
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return digest.hex()[:32]


def verify_approval_token(message_id: int, token: str) -> bool:
    """Constant-time HMAC compare. Returns False on any mismatch or missing secret."""
    if not settings.INTERNAL_API_TOKEN or not token:
        return False
    try:
        expected = make_approval_token(message_id)
    except ApprovalError:
        return False
    return hmac.compare_digest(expected, token)


def approve(message_id: int, approver: str, edited_body: str | None = None) -> Message:
    """Approve a message, optionally editing the body. Returns the updated message."""
    session = SessionLocal()
    try:
        msg = session.get(Message, message_id)
        if not msg:
            raise ApprovalError(f"Message {message_id} not found.")
        if msg.status != "pending_approval":
            raise ApprovalError(f"Message {message_id} is {msg.status}, not pending_approval.")

        action = "edit" if edited_body else "approve"
        diff = None
        if edited_body:
            diff = edited_body
            msg.body = edited_body

        msg.status = "approved"
        msg.approved_by = approver
        msg.approved_at = datetime.now(timezone.utc)

        session.add(
            Approval(
                message_id=message_id,
                approver=approver,
                action=action,
                diff=diff,
            )
        )
        session.commit()
        session.refresh(msg)
        logger.info("Message %d approved by %s.", message_id, approver)
        return msg
    finally:
        session.close()


def reject(message_id: int, approver: str, reason: str | None = None) -> Message:
    """Reject a message."""
    session = SessionLocal()
    try:
        msg = session.get(Message, message_id)
        if not msg:
            raise ApprovalError(f"Message {message_id} not found.")
        if msg.status != "pending_approval":
            raise ApprovalError(f"Message {message_id} is {msg.status}, not pending_approval.")

        msg.status = "rejected"

        session.add(
            Approval(
                message_id=message_id,
                approver=approver,
                action="reject",
                reason=reason,
            )
        )
        session.commit()
        session.refresh(msg)
        logger.info("Message %d rejected by %s. Reason: %s", message_id, approver, reason)
        return msg
    finally:
        session.close()


def mark_sent(message_id: int) -> None:
    """Mark a message as sent after successful delivery, and move the linked HubSpot
    ticket forward in its pipeline (if configured).
    """
    ticket_id: str | None = None
    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        if msg:
            msg.status = "sent"
            msg.sent_at = datetime.now(timezone.utc)
            conv = session.get(Conversation, msg.conversation_id) if msg.conversation_id else None
            ticket_id = conv.hubspot_ticket_id if conv else None
            session.commit()

    if ticket_id:
        from ..integrations.hubspot import move_ticket_stage_after_send

        move_ticket_stage_after_send(ticket_id)
