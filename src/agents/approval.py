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

    **보통은 False 입니다** — 초안은 처음부터 나갈 언어로 쓰입니다. 이 함수가 남아 있는 것은
    두 경우 때문입니다: ① 초안을 쓰는 모델이 지시를 어기고 한국어로 썼고 그 자리의 번역도
    실패했다 ② 운영자가 검토 화면에서 본문을 한국어로 고쳐 놓았다. 어느 쪽이든 한국어 메일이
    영어 고객에게 가는 길이라, 번역하기를 한 번 거치게 합니다.

    발송은 번역기를 부르는 자리가 아닙니다 — 사람이 못 본 글이 나가면 안 됩니다.
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
    channel_account_id: str | None | object = _UNSET,
) -> Message:
    """Atomically freeze the operator-reviewed message and approve it.

    ``signature_key`` 를 안 넘기면 행의 값을 그대로 둡니다. **넘기면 그 값으로 씁니다 —
    None 이어도.** None 은 「서명 없음」이고, 그것도 운영자가 고른 것입니다.

    ``channel_account_id`` 도 같은 규칙입니다(이관 0105) — 운영자가 고른 **발신 주소**이고,
    None 은 「고르지 않음」이라 스레드가 정하는 예전 동작입니다.
    """
    session = SessionLocal()
    try:
        pending = session.get(Message, message_id)
        if not pending:
            raise ApprovalError(f"Message {message_id} not found.")
        # ``send_failed`` 도 승인할 수 있습니다 — **재발송이 곧 재승인**입니다.
        # 발송이 실패했다는 것은 고객에게 아무것도 안 갔다는 뜻이고(400 이면 HubSpot 이
        # 아무것도 만들지 않습니다), 그 초안을 다시 보내겠다는 판단은 처음 보내겠다는 판단과
        # 같은 종류입니다. 그래서 사람이 승인한다는 대전제는 그대로입니다.
        # ``delivery_unknown`` 은 **넣지 않습니다**: 그건 「갔는지 모른다」라서 다시 보내면
        # 고객이 같은 메일을 두 번 받을 수 있고, 그 판단은 복구 화면의 「발송됨 확인 /
        # 미발송 확인」이 따로 받습니다.
        if pending.status not in {"pending_approval", "send_failed"}:
            raise ApprovalError(
                f"Message {message_id} is {pending.status}, not pending_approval."
            )
        candidate_body = edited_body if edited_body is not None else pending.body
        # **빈 글은 못 나갑니다.** 수동 후속 회신은 본문 없이 만들어지고(운영자가 검토
        # 화면에서 씁니다), 그 상태로 발송을 누르면 고객에게 빈 메일이 갑니다. 자동 초안은
        # 여기 걸릴 일이 없습니다.
        if not (candidate_body or "").strip():
            raise ApprovalError("본문이 비어 있습니다.")
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
        if channel_account_id is not _UNSET:
            values["channel_account_id"] = channel_account_id

        result = session.execute(
            update(Message)
            .where(
                Message.id == message_id,
                Message.status.in_(["pending_approval", "send_failed"]),
            )
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
        # 발송이 실패한 초안도 거절할 수 있습니다 — 「이건 안 보낸다」는 결정은 실패
        # 전후로 같은 결정입니다. 복구 화면의 「발송 실패 정리」가 이미 같은 일을 묶음으로
        # 합니다.
        result = session.execute(
            update(Message)
            .where(
                Message.id == message_id,
                Message.status.in_(["pending_approval", "send_failed"]),
            )
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
            # **앞으로만 갑니다** — 워커 경로(`send_worker._ADVANCES_FROM`)와 같은 규칙입니다.
            # 협상·수주 건에 후속 회신을 보냈다고 티켓을 Qualified 로 되돌리면 안 됩니다.
            from .send_worker import _ADVANCES_FROM

            if conv and conv.stage in _ADVANCES_FROM:
                ticket_id = conv.hubspot_ticket_id
            session.commit()

    if ticket_id:
        from ..integrations.hubspot import move_ticket_stage_after_send

        move_ticket_stage_after_send(ticket_id)
