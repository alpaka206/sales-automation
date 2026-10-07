"""Read-only, customer-scoped history shared by ticket, lead and won screens."""
from __future__ import annotations

from datetime import datetime
from email.utils import parseaddr

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


# 이메일 채널의 두 철자 — 허브스팟 스레드 수집기 · 개인함은 「이메일」, CRM 수집기 · 콘솔의 손 기록은 `email`.
EMAIL_CHANNELS = frozenset({"이메일", "email"})
_FORM_CHANNELS = frozenset({"폼", "form"})
_LEGACY_DIRECTIONS = {"incoming": "inbound", "outbound": "outgoing"}


def non_sales_senders() -> frozenset[str]:
    """영업이 아닌 우리 주소(`NON_SALES_SENDER_ADDRESSES`). 그 주소의 메일은 CS 안내이지 우리 회신이 아니다.

    **거부 목록이다.** 주소를 모르는 줄(CRM 줄 · 손 기록 · 발신 주소 없는 수집 줄)은 영업으로 센다 —
    허용 목록으로 두면 새 영업 주소가 생길 때마다 그 사람의 회신이 조용히 「아직 아무도 답 안 함」이 된다.
    """
    from ..common.config import settings

    return frozenset(
        address.strip().lower()
        for address in (settings.NON_SALES_SENDER_ADDRESSES or "").split(",")
        if address.strip()
    )


def turn_role(row, *, non_sales: frozenset[str] | None = None) -> str:
    """그 줄을 **누가** 말했나 — `customer` · `sales` · `cs` · `bot` · `form` · `note` · `reminder`.

    방향(`direction`)만으로는 모자랐다(2026-10-06 운영 재생): 챗봇 답(채팅 · `B-`)과 CS 주소
    (`support@perso.ai`)의 안내도 수집기에는 「우리가 보낸 것」이라, 영업이 한 통도 안 보낸 티켓 42건이
    「이미 답한 티켓」으로 읽혔다 — 첫 회신 문서 대신 후속 회신 문서를 받고, 고객 질문이 봇 답 뒤에
    가려졌다. 방향은 그대로 두고(그건 「고객 말인가」의 답이다) 역할을 따로 잰다.

    - 콘솔 행(`messages`): 문의는 고객 · 나간(`sent`) 회신은 영업 — 운영자가 이 화면에서 승인했다. 보낸
      주소(폼 스레드 폴백의 support@perso.ai 같은)는 묻지 않는다. 리마인더는 `reminder`, 그 외(초안 ·
      `test_sent` · 갔는지 모르는 것 · 옛 접수확인)는 고객이 받은 회신이 아니라 `note`.
    - 가져온 줄 · 손 기록(`customer_interactions`): 채팅의 우리 쪽은 `bot`(사람 상담원도 섞이지만 수집기가
      액터를 안 남겨 가를 수 없고, 어느 쪽이든 영업 메일이 아니다), 이메일은 보낸 주소가 거부 목록에
      있으면 `cs` 아니면 `sales`, 그 밖의 우리 쪽 기록(전화 · 미팅 · 메모)은 `note`.
    """
    from ..agents.followup_sequence import REMINDER_NOTE_PREFIX, REMINDER_VARIANTS

    if isinstance(row, Message):
        if row.direction == "inbound":
            return "form" if row.channel in _FORM_CHANNELS else "customer"
        if row.prompt_variant in REMINDER_VARIANTS:
            return "reminder"
        if row.direction == "outgoing" and row.status == "sent" and row.prompt_variant != "auto_ack":
            return "sales"
        return "note"
    if (row.external_id or "").startswith(REMINDER_NOTE_PREFIX):
        return "reminder"
    direction = _LEGACY_DIRECTIONS.get(row.direction or "", row.direction or "")
    if direction == "inbound":
        return "form" if row.channel in _FORM_CHANNELS else "customer"
    if direction != "outgoing":
        return "note"
    if row.channel == "채팅":
        return "bot"
    if row.channel in EMAIL_CHANNELS:
        sender = parseaddr(row.handler or "")[1].strip().lower()
        denied = non_sales_senders() if non_sales is None else non_sales
        return "cs" if sender in denied else "sales"
    return "note"


def turn_origin(row) -> str:
    """그 줄이 어디서 왔나 — `console` · `hubspot`(스레드 수집) · `crm`(옛 CRM 메일 수집) · `gmail`(개인함) ·
    `reminder_note` · `manual`(손 기록)."""
    from ..agents.followup_sequence import REMINDER_NOTE_PREFIX

    if isinstance(row, Message):
        return "console"
    ext = row.external_id or ""
    for prefix, origin in (("hubspot:conv:", "hubspot"), ("hubspot:email:", "crm"), ("gmail:", "gmail"),
                           (REMINDER_NOTE_PREFIX, "reminder_note")):
        if ext.startswith(prefix):
            return origin
    return "manual" if not ext else "other"


def is_sales_email(row, *, non_sales: frozenset[str] | None = None) -> bool:
    """**우리 영업이 실제로 이메일로 보낸 회신**인가 — 「우리가 마지막으로 한 말」을 재는 자는 이것 하나다.

    부르는 곳: 첫 회신 판정 · 지난 회신 앵커 · 「마지막 회신 뒤 고객 메시지」(`inbound.thread_events` 의
    `role`), 답장 기준선(`ticket_history.advance_if_customer_replied`), 후속 리마인더 시계
    (`followup_sequence` — 그쪽은 여기에 「열쇠가 있는 메일」을 더 요구한다). 예전에는 다섯이 각자 「나간
    우리 줄」을 셌고, 챗봇 · CS 줄을 세는 곳과 안 세는 곳이 갈렸다.

    조건: 이메일 · 우리가 보냄 · 고객에게 닿음(콘솔 `sent` / 가져온 메일 · 손으로 적은 메일 기록) · 리마인더 ·
    접수확인 · 리마인더 기록 줄이 아님 · 보낸 주소가 거부 목록(`non_sales_senders`)에 없음. **사본은 부르는
    쪽이 먼저 거른다** — 대화는 `message_copies`, 리마인더 시계는 자기 열쇠 묶음(`outside_replies`).
    """
    return turn_role(row, non_sales=non_sales) == "sales"


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
