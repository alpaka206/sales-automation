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


def translation_required(message: Message, body: str | None = None) -> bool:
    """Whether a draft must pass through the review-screen translation step.

    Foreign-language replies start as Korean review drafts. The operator must press
    번역하기 and review the result before approval; delivery is never the place to call
    the translator. The script check also catches a draft edited back to Korean after
    it had previously been translated.
    """
    target = (message.target_language or "").strip().lower()
    if not target or target == "ko":
        return False
    current = (message.language or "").strip().lower()
    candidate = message.body if body is None else body

    from ..llm.translate import is_mostly_korean

    return current != target or is_mostly_korean(candidate)


# "안 넘겼다" 와 "없음으로 정했다" 를 가릅니다. 서명은 None 이 곧 「서명 없음」이라, 기본값을
# None 으로 두면 그 둘이 같은 값이 되어 **운영자가 고른 「서명 없음」이 무시됐습니다** —
# 초안이 만들어질 때 달린 기본 서명이 그대로 붙어 나갔습니다. 같은 폼의 `저장`·`번역하기`는
# 직접 대입이라 지워졌고, 그래서 한 번 경유하면 지워지고 곧장 발송하면 안 지워졌습니다.
_UNSET: object = object()


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
    signature_key: str | None | object = _UNSET,
) -> Message:
    """Atomically freeze the operator-reviewed message and approve it.

    ``signature_key`` 를 안 넘기면 행의 값을 그대로 둡니다. **넘기면 그 값으로 씁니다 —
    None 이어도.** None 은 「서명 없음」이고, 그것도 운영자가 고른 것입니다.
    """
    session = SessionLocal()
    try:
        pending = session.get(Message, message_id)
        if not pending:
            raise ApprovalError(f"Message {message_id} not found.")
        if pending.status != "pending_approval":
            raise ApprovalError(
                f"Message {message_id} is {pending.status}, not pending_approval."
            )
        candidate_body = edited_body if edited_body is not None else pending.body
        if translation_required(pending, candidate_body):
            raise ApprovalError(
                "외국어 문의는 번역하기를 완료하고 번역문을 검토한 뒤 발송할 수 있습니다."
            )

        values: dict[str, object] = {
            "status": "approved",
            "approved_by": approver,
            "approved_at": datetime.now(timezone.utc),
        }
        if edited_body is not None:
            values["body"] = edited_body
        if edited_subject is not None:
            values["subject"] = edited_subject
        if signature_key is not _UNSET:
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
