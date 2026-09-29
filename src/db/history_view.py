"""Read-only, customer-scoped history shared by ticket, lead and won screens."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from .conversation_history import ROUTINE_PROGRESS_KINDS
from .models import DELIVERED_STATUSES, Conversation, ConversationProgress, CustomerInteraction, Message


# 가져온 줄(허브스팟 스레드 · CRM 메일 · 개인함)만 사본일 수 있다 — 사람이 손으로 적은 기록은 그 사람의 말이다.
_COPY_PREFIXES = ("hubspot:conv:", "hubspot:email:", "gmail:")


def message_copies(messages, interactions) -> dict[int, object]:
    """{접점 기록 id: 그 줄이 사본인 `messages` 행} — 한 메일을 두 번 세지 않게.

    읽는 곳이 넷이다: 티켓 화면(`routes.messages`), 고객·리드·수주 기록(`ticket_records`), 초안이 읽는
    대화(`inbound.thread_events`), 티켓 요약 재생성(`summaries.rebuild_summary`). 예전에는 넷이 각자
    `hubspot_message_id` 하나로 쟀고, **그 열쇠가 없는 행은 넷 다 두 번 셌다** (2026-09-29 감사):

    - 접수가 만든 **첫 문의** — 스레드 id 를 모른다. 스레드 수집이 같은 문의를 `hubspot:conv:` 로 넣는다.
    - 결과를 모른 채 끝난 발송(`delivery_unknown`)과, 그것을 복구 화면에서 「나갔다」로 확인한 것.

    열쇠가 있으면 열쇠(허브스팟 id · 개인함 지메일 id), 없으면 본문 — `ticket_history.same_mail`, 개인함과
    허브스팟 사본을 가르는 그 자다. 폼 문의의 허브스팟 사본은 폼 칸을 늘어놓은 글(`Service: … Details: …`)
    이라 본문이 통째로 같지 않다 — 문의 본문을 **담고 있으면** 사본이다.
    """
    from ..agents.ticket_history import SAME_MAIL_WINDOW, same_mail

    keys: set[str] = set()
    loose = []  # 열쇠 없이 본문으로 알아봐야 하는 행
    for m in messages:
        if m.hubspot_message_id:
            keys.add(f"hubspot:conv:{m.hubspot_message_id}")
        if m.smtp_message_id:
            keys.add(f"gmail:{m.smtp_message_id}")
        # 본문으로 맞추는 것은 고객에게 **간** 글과 고객이 쓴 글뿐 — 초안에는 사본이 있을 수 없다.
        delivered = m.direction == "inbound" or m.status in DELIVERED_STATUSES
        if delivered and not (m.hubspot_message_id or m.smtp_message_id) and (m.sent_at or m.created_at):
            loose.append(m)
    copies: dict[int, object] = {}
    for item in interactions:
        ext = item.external_id or ""
        if ext in keys:
            copies[item.id] = next(m for m in messages if ext in (
                f"hubspot:conv:{m.hubspot_message_id}", f"gmail:{m.smtp_message_id}"))
            continue
        if not loose or item.happened_at is None or not ext.startswith(_COPY_PREFIXES):
            continue
        for m in loose:
            when = m.sent_at or m.created_at
            if same_mail(when, m.direction, m.body, item.happened_at, item.direction, item.summary):
                copies[item.id] = m
                break
            if (item.channel == "폼" and m.direction == "inbound"
                    and abs(when.replace(tzinfo=None) - item.happened_at.replace(tzinfo=None)) <= SAME_MAIL_WINDOW):
                body = _squash(m.body)
                if len(body) >= 20 and body[:200] in _squash(item.summary):
                    copies[item.id] = m
                    break
    return copies


def _squash(text: str | None) -> str:
    return " ".join((text or "").split()).lower()


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
    # 같은 메일을 두 번 세지 않습니다 — 자는 `message_copies` 하나입니다(`inbound.thread_events` 와
    # 같은 규칙). 리마인더의 「Reminder Sent N」 줄(`followup:reminder:<id>`)은 **거르지 않습니다**:
    # 그 줄은 운영자 지시로 소통 히스토리에 남기는 것이고(2026-09-21), 티켓 화면이 그리는 것을
    # 고객 상세가 숨기면 두 화면이 같은 행을 두고 다른 말을 합니다.
    interactions = list(session.scalars(select(CustomerInteraction).where(
        CustomerInteraction.contact_id == contact_id, CustomerInteraction.conversation_id.in_(ids),
    )))
    drawn = message_copies(messages, interactions)
    for msg in messages:
        result[msg.conversation_id].append({
            "record_key": f"message:{msg.id}", "conversation_id": msg.conversation_id,
            "channel": msg.channel, "direction": msg.direction, "handler": None,
            "subject": msg.subject, "summary": msg.body, "context": msg.summary_line,
            "happened_at": msg.sent_at or msg.created_at, "source": "message",
            "editable": False,
        })
    for item in interactions:
        if item.id not in drawn:
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
