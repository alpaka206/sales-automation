"""Read-only, customer-scoped history shared by ticket, lead and won screens."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from .conversation_history import ROUTINE_PROGRESS_KINDS
from .models import DELIVERED_STATUSES, Conversation, ConversationProgress, CustomerInteraction, Message


def interaction_record(row: CustomerInteraction) -> dict:
    return {
        "record_key": f"interaction:{row.id}", "id": row.id,
        "conversation_id": row.conversation_id, "editable": row.external_id is None,
        "channel": row.channel, "direction": row.direction, "handler": row.handler,
        "subject": row.subject, "summary": row.summary, "context": row.context,
        "happened_at": row.happened_at, "source": "interaction",
    }


def ticket_records(session, contact_id: int, conversation_ids: list[int]) -> dict[int, list[dict]]:
    # Do not trust a caller's IDs to define the customer's data boundary.
    ids = list(session.scalars(select(Conversation.id).where(
        Conversation.contact_id == contact_id, Conversation.id.in_(conversation_ids),
    )))
    result: dict[int, list[dict]] = {cid: [] for cid in ids}
    if not ids:
        return result
    # 나간 회신의 기준은 `DELIVERED_STATUSES` 한 곳입니다 — 티켓 화면의 대화(`messages.py`
    # `_DELIVERED`)와 같은 집합. `sent` 만 보면 5xx·타임아웃으로 「갔는지 모르는」 회신
    # (`delivery_unknown`)이 이력에서 사라지는데, 그 메일은 이미 고객에게 닿았을 수 있습니다.
    messages = list(session.scalars(select(Message).where(
        Message.conversation_id.in_(ids),
        (Message.direction == "inbound") | Message.status.in_(DELIVERED_STATUSES),
    )))
    # 같은 메일을 두 번 세지 않습니다 — 열쇠는 짐작이 아니라 허브스팟 메시지 id 입니다
    # (`inbound.thread_events` 와 같은 규칙). 리마인더의 「Reminder Sent N」 줄
    # (`followup:reminder:<id>`)은 **거르지 않습니다**: 그 줄은 운영자 지시로 소통 히스토리에
    # 남기는 것이고(2026-09-21), 티켓 화면이 그리는 것을 고객 상세가 숨기면 두 화면이 같은
    # 행을 두고 다른 말을 합니다.
    drawn = {f"hubspot:conv:{m.hubspot_message_id}" for m in messages if m.hubspot_message_id}
    for msg in messages:
        result[msg.conversation_id].append({
            "record_key": f"message:{msg.id}", "conversation_id": msg.conversation_id,
            "channel": msg.channel, "direction": msg.direction, "handler": None,
            "subject": msg.subject, "summary": msg.body, "context": msg.summary_line,
            "happened_at": msg.sent_at or msg.created_at, "source": "message",
            "editable": False,
        })
    for item in session.scalars(select(CustomerInteraction).where(
        CustomerInteraction.contact_id == contact_id, CustomerInteraction.conversation_id.in_(ids),
    )):
        if item.external_id not in drawn:
            result[item.conversation_id].append(interaction_record(item))
    for row in session.scalars(select(ConversationProgress).where(
        ConversationProgress.conversation_id.in_(ids),
        ConversationProgress.kind.not_in(ROUTINE_PROGRESS_KINDS),
    )):
        result[row.conversation_id].append({
            "record_key": f"progress:{row.id}", "conversation_id": row.conversation_id,
            "channel": "manual", "direction": "note", "handler": None,
            "subject": None, "summary": row.detail, "context": None,
            "happened_at": row.created_at, "source": "progress", "editable": False,
        })
    for records in result.values():
        records.sort(key=lambda row: (row["happened_at"] or datetime.min, row["record_key"]),
                     reverse=True)
    return result
