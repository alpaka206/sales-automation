"""Local snapshot checks for reviewed replies. No model output grants permission."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from ..db.models import Event, Message
from ..llm.policy_context import PolicySnapshot, fingerprint


def latest_trace(session, kind: str, message_id: int) -> dict | None:
    row = session.scalar(select(Event).where(
        Event.kind == kind, Event.payload["message_id"].as_integer() == message_id,
    ).order_by(Event.id.desc()).limit(1))
    return row.payload if row else None


def approval_binding(message: Message, overrides: dict | None = None) -> str:
    overrides = overrides or {}
    fields = ("conversation_id", "body", "subject", "to_address", "from_address", "channel",
              "signature_key", "channel_account_id", "cc_addresses", "language", "target_language")
    return fingerprint({key: overrides.get(key, getattr(message, key)) for key in fields})


def validate_draft_context(message_id: int) -> None:
    from ..db.session import SessionLocal
    from .inbound import customer_turns_since, thread_events

    with SessionLocal() as session:
        trace = latest_trace(session, "reply_context", message_id)
    if trace is None:
        return  # Legacy/manual drafts and template reminders have no generated context.
    current = PolicySnapshot.capture(trace["policy"]["stage"])
    if current.digest != trace["policy"]["sha256"]:
        raise RuntimeError("정책이 변경되었습니다. 초안을 다시 생성하고 검토해 주세요.")
    # 초안 뒤에 온 **고객** 메시지만 봅니다 — 대화 전체 해시가 아닙니다. 스레드 수집이
    # 넣는 최초 문의의 사본, 운영자의 「수신」 기록, 개인함에서 붙는 옛 메일은 초안이 답할
    # 새 말이 아닌데, 해시로 재면 그것들이 전부 「대화 변경」이 됩니다
    # (`inbound.customer_turns_since` 의 docstring).
    since = datetime.fromisoformat(trace["generated_at"])
    if customer_turns_since(thread_events(trace["conversation_id"]), since, seen=trace["turn_refs"]):
        raise RuntimeError("대화가 변경되었습니다. 초안을 다시 생성하고 검토해 주세요.")


def validate_approved_message(message: Message, *, transformed: bool = False) -> None:
    from ..db.session import SessionLocal

    with SessionLocal() as session:
        trace = latest_trace(session, "reply_approval", message.id)
        if trace is not None:
            stored = session.get(Message, message.id)
            # 워커가 행을 잡으면 status 가 `sending:<pid>:<random>` 입니다
            # (`send_worker._WORKER_ID` — 그 값이 곧 행 잠금). `"sending"` 한 글자와
            # 비교하면 사람이 승인한 회신이 **전부** 여기서 send_failed 가 됩니다.
            # 로컬에서는 안 잡힙니다: 안전 모드는 이 검사 앞에서 SendingDisabled 로 빠집니다.
            claimed = (stored.status or "").startswith("sending:") if stored else False
            if (stored is None or (stored.status != "approved" and not claimed)
                    or (not transformed and approval_binding(message) != trace["binding"])
                    or approval_binding(stored) != trace["binding"]):
                raise RuntimeError("승인 후 발송 내용 또는 수신 정보가 변경되었습니다.")
    validate_draft_context(message.id)
