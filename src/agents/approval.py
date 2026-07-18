"""Approval handler - processes approve/edit/reject actions on messages."""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone

from sqlalchemy import update

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


def approve(
    message_id: int,
    approver: str,
    edited_body: str | None = None,
    *,
    edited_subject: str | None = None,
    signature_key: str | None = None,
) -> Message:
    """Atomically freeze the operator-reviewed message and approve it."""
    session = SessionLocal()
    try:
        values: dict[str, object] = {
            "status": "approved",
            "approved_by": approver,
            "approved_at": datetime.now(timezone.utc),
        }
        if edited_body is not None:
            values["body"] = edited_body
        if edited_subject is not None:
            values["subject"] = edited_subject
        if signature_key is not None:
            values["signature_key"] = signature_key

        result = session.execute(
            update(Message)
            .where(Message.id == message_id, Message.status == "pending_approval")
            .values(**values)
        )
        if result.rowcount != 1:
            session.rollback()
            msg = session.get(Message, message_id)
            if not msg:
                raise ApprovalError(f"Message {message_id} not found.")
            raise ApprovalError(f"Message {message_id} is {msg.status}, not pending_approval.")

        action = "edit" if edited_body is not None else "approve"

        session.add(
            Approval(
                message_id=message_id,
                approver=approver,
                action=action,
                diff=edited_body if edited_body is not None else None,
            )
        )
        session.commit()
        msg = session.get(Message, message_id)
        assert msg is not None
        session.refresh(msg)
        logger.info("Message %d approved by %s.", message_id, approver)
        return msg
    finally:
        session.close()


def reject(message_id: int, approver: str, reason: str | None = None) -> Message:
    """Reject a message only while it is still awaiting approval."""
    session = SessionLocal()
    try:
        result = session.execute(
            update(Message)
            .where(Message.id == message_id, Message.status == "pending_approval")
            .values(status="rejected")
        )
        if result.rowcount != 1:
            session.rollback()
            msg = session.get(Message, message_id)
            if not msg:
                raise ApprovalError(f"Message {message_id} not found.")
            raise ApprovalError(f"Message {message_id} is {msg.status}, not pending_approval.")

        session.add(
            Approval(
                message_id=message_id,
                approver=approver,
                action="reject",
                reason=reason,
            )
        )
        session.commit()
        msg = session.get(Message, message_id)
        assert msg is not None
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
