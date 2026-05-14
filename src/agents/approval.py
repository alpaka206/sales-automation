"""Approval handler - processes approve/edit/reject actions on messages."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..db.models import Approval, Message
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)


class ApprovalError(RuntimeError):
    pass


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
    """Mark a message as sent after successful delivery."""
    session = SessionLocal()
    try:
        msg = session.get(Message, message_id)
        if msg:
            msg.status = "sent"
            msg.sent_at = datetime.now(timezone.utc)
            session.commit()
    finally:
        session.close()
