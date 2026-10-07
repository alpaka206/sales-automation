"""Contacted 후속 리마인더 — 답이 없으면 3일 · 5일 · 7일 (2026-09-17 운영자 지시).

허브스팟 워크플로(`4623059693`)가 하던 일을 옮겨 왔다. 설계와 근거는
`docs/후속-회신-시퀀스-설계.md` — 초안을 세 방향에서 코드에 맞대어 깨뜨린 뒤의 판이다.

| 기준 | 답이 없으면 |
|---|---|
| 우리 회신 +3일 | 리마인더 1 (템플릿 `followup_reminder`) |
| 리마인더 1 +5일 | 리마인더 2 (템플릿 `followup_closing`) |
| 리마인더 2 +7일 | Concluded (종결) |

그 사이 언제든 고객이 연락하면 Negotiating 으로 옮기고 끝. **닫은 뒤에** 연락이 와도
Negotiating 으로 되살리고, 그 티켓은 화면에 빨갛게 선다(`followup_closed_at`).

**닫는 단계는 Concluded 다 — Closed Lost 가 아니다** (2026-09-30 운영자 지시: 「리마인더 메일 다
끝나면 concluded로 가야하는데」). 처음 지시(09-17)는 「7일동안 안오면 closed lost」였고 그대로
만들었다. 답이 없어 끝난 문의는 진 건(Closed Lost)이 아니라 끝난 문의(Concluded — 옛 No Response 를
이관 0076 이 접어 넣은 그 단계)다.

**새 표도 새 발송 경로도 없다.**
- 리마인더는 `messages` 행이다. 몇 번째를 보냈는지는 그 행들에서 읽는다(`sequence_state`) —
  저장하면 원본이 바뀔 때 조용히 어긋난다.
- 행을 `approved` 로 세우면 켜져 있는 발송 워커가 보낸다. 그래서 안전 관문 · 스레드 고르기 ·
  발신 주소(개인 사서함 포함) · CC · 서명 · 실패 사유가 사람이 누른 발송과 한 글자도 다르지
  않다. **「모든 회신은 사람이 승인한다」의 유일한 예외**이고, 승인에 해당하는 것은 운영자가
  콘솔에 템플릿을 쓴 행위다.
- 시계의 기준은 **우리가 마지막으로 보낸 이메일**이다 — 이 콘솔에서 나간 회신, **허브스팟
  받은편지함 화면에서 보낸 회신, 연결된 개인 사서함에서 보낸 메일** 전부(`outside_replies`,
  2026-09-28). 단계 이동 시각은 기록하지 않고(2026-08-20 지시), 회신과 Contacted 이동은 같은
  사건이다. 운영자가 후속 회신을 또 보내면 기준이 그리로 옮겨 가고 3·5·7 이 처음부터 다시 간다.
  - 예전에는 콘솔 회신만 셌다. 「회신은 콘솔에서만 나간다」가 전제였는데 사실이 아니었다 —
    2026-09-23 의 두 티켓(424·425)이 허브스팟 화면에서 답하고 Contacted 로 옮겨졌고, 시퀀스에
    **아예 안 들어가서** 나흘이 지나도 리마인더가 없었고 화면에는 칩도 안 섰다(운영자 보고).
    「고객 답장 → Negotiating」은 이미 세 길을 같은 자(`inbound.thread_events`)로 쟀는데 이
    시계만 한 길을 봤다.

`FOLLOWUP_SEQUENCE_SINCE` 가 비어 있으면 아무것도 안 한다. 그 날짜 뒤에 나간 회신만 시계를
돈다 — 기존 티켓은 안 건드린다는 지시가 그 한 칸이다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from ..common.config import settings
from ..common.subjects import choose_reply_subject
from ..db.history_view import EMAIL_CHANNELS, is_sales_email, non_sales_senders
from ..db.models import Contact, Conversation, CustomerInteraction, MailboxAccount, Message
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

REMINDER_1 = "followup_reminder_1"
REMINDER_2 = "followup_reminder_2"
REMINDER_VARIANTS = (REMINDER_1, REMINDER_2)
REMINDER_ORDINAL = {REMINDER_1: 1, REMINDER_2: 2}
# 아직 한 통도 안 나간 시퀀스의 칩. `done_label` 과 같은 자리(티켓 배너 · 보드 카드)에 선다.
PENDING_LABEL = "Pending"
# 발송 경로가 이 이름으로 찾는다(`db.email_templates._CODE_RESOLVED_KEYS`). 본문은 운영자가 콘솔에
# 쓴 영문 그대로이고, 고객 언어가 영어가 아니면 보낼 때 번역한다.
TEMPLATE_KEYS = {REMINDER_1: "followup_reminder", REMINDER_2: "followup_closing"}
APPROVER = "followup_sequence"
# 리마인더가 나간 뒤 소통 히스토리에 남기는 줄의 `external_id` 앞머리(`send_worker`).
# **두 곳이 이 글자를 안다** — 남기는 쪽과, 대화를 읽을 때 그 줄을 건너뛰는 쪽
# (`inbound.thread_events`). 철자를 두 파일에 따로 적으면 한쪽만 고쳐질 때 그 줄이 조용히
# 「우리가 보낸 회신」으로 다시 세어집니다.
REMINDER_NOTE_PREFIX = "followup:reminder:"

CONTACTED = "meeting_link_sent"
NEGOTIATION = "negotiation"
# 답이 없어 시퀀스가 끝나면 옮기는 단계 — 로컬 키 `closed`, 화면 이름 Concluded(허브스팟 1404814097).
# 2026-09-30 까지는 `closed_lost` 였다(모듈 설명). 되살리기도 이 단계에 있는 티켓만 본다 — 사람이 그 뒤
# Closed Lost 로 옮긴 것은 사람의 결정이라 안 되살린다.
CLOSE_STAGE = "closed"

AFTER_REPLY = timedelta(days=3)
AFTER_REMINDER_1 = timedelta(days=5)
AFTER_REMINDER_2 = timedelta(days=7)
# 한 회차에 보내거나 닫는 수. 읽기는 대상 수와 무관하게 쿼리 일곱이다(후보 · 메일 · 콘솔 밖 메일 둘 ·
# 답장 둘 · 끊긴 사서함). 행동하기 직전의 재확인만 대상마다 따로다.
PER_SWEEP = 20
_KST = timezone(timedelta(hours=9))
# 콘솔 밖에서 나간 우리 메일의 두 출처 — 허브스팟 스레드 수집기(`ticket_history`)와 개인 사서함
# 수집기(`mailbox_sync`)가 `customer_interactions` 에 이 앞머리로 남긴다. 리마인더가 「지난 메일」을 베끼려면
# 그 메일을 다시 찾을 열쇠가 있어야 한다 — 「우리 영업 메일인가」(`history_view.is_sales_email`)에 이 시계만
# 더 요구하는 조건이다. 채널 말은 공용 철자 묶음(`EMAIL_CHANNELS`)이다.
_OUTSIDE_PREFIXES = ("hubspot:conv:", "gmail:")
_MAILBOX_SUFFIX = " 개인 메일함"  # `mailbox_sync` 가 `context` 에 적는 「<사서함> 개인 메일함」


def done_label(variant: str) -> str:
    """「Reminder Sent 1」 — 티켓 배너 · 보드 카드 · 소통 히스토리 · 진행 기록이 **같은 한 문장**을 적는다.

    글자는 운영자의 것이다(2026-09-22: 「리마인더 센트 기본적으로 떠있게(Pendding, Reminder Sent 1,
    Reminder Sent 2)」 — 전날의 「1차 리마인더 완료」를 대신한다). 변형 이름을 이 모듈이 들고
    있으니 그 말도 여기가 들고 있다. 두 곳에 적으면 네 화면이 같은 사건을 다르게 부른다.
    """
    return f"Reminder Sent {REMINDER_ORDINAL[variant]}"


def reminder_status(messages, outside=()) -> str:
    """칩 한 장: 실제로 나간 리마인더 중 가장 높은 것의 `done_label`, 없으면 `PENDING_LABEL`.

    「보냈다」의 기준은 `next_step` 이 「이미 보냈다」로 쓰는 그 조건이다(`sent` + `sent_at`).
    `sent_at` 만 보면 `test_sent`(SAFE 모드로 나간 것 — 고객이 받은 것이 없다)가 완료로 읽히고,
    시퀀스는 `stalled` 인데 화면만 「Reminder Sent 1」 이라 적는다.
    """
    _base, reminders = sequence_state(messages, outside)
    label = PENDING_LABEL
    for variant in REMINDER_VARIANTS:
        row = reminders.get(variant)
        if row is not None and row.status == "sent" and row.sent_at is not None:
            label = done_label(variant)
    return label


def _naive(value: datetime | None) -> datetime | None:
    """열은 tz 없는 UTC 다. 새로 만드는 시각은 aware 라 비교 전에 떼어 맞춘다."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def since() -> datetime | None:
    """그 날짜 **한국 시각 0시**(UTC 로 전날 15시). 열이 UTC 라 그대로 두면 그날 오전 9시 전에
    나간 회신이 「기존 티켓」으로 빠진다."""
    raw = (settings.FOLLOWUP_SEQUENCE_SINCE or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d") - timedelta(hours=9)
    except ValueError:
        logger.warning("FOLLOWUP_SEQUENCE_SINCE 가 YYYY-MM-DD 가 아닙니다 — 리마인더를 끕니다.")
        return None


def in_send_window(now: datetime) -> bool:
    """평일 09~18시(KST)에만 보낸다. 정확할 필요는 없고(운영자) 한밤중·주말에 안 나가면 된다.

    6시간 wake 는 KST 09·15시에 창 안으로 들어오므로, 서비스가 자고 있어도 늦어야 반나절이다.
    """
    local = now.replace(tzinfo=timezone.utc).astimezone(_KST)
    return local.weekday() < 5 and 9 <= local.hour < 18


def outside_replies(session, conversation_ids) -> dict[int, list[CustomerInteraction]]:
    """{대화: 이 콘솔 **밖에서** 나간 우리 이메일} — 허브스팟 화면 회신과 개인 사서함 메일. 쿼리 둘.

    **자는 `inbound.thread_events` 와 같다.** 콘솔에서 나간 메일은 수집기가 허브스팟(또는 사서함)에서
    도로 가져와 두 표에 다 있으므로 같은 열쇠(`messages.hubspot_message_id` · `smtp_message_id`)로
    한 번만 센다. 리마인더도 그 열쇠를 가지므로 여기서 빠진다 — 안 빠지면 리마인더의 사본이 새
    기준이 되어 **3일마다 1차가 다시 나간다.** 열쇠를 못 받았는데 메일이 나갔을 수 있는 리마인더
    (`delivery_unknown` · 보내는 중)의 사본은 **본문**으로 알아본다 — `ticket_history.same_mail`, 개인함과
    허브스팟 사본을 가르는 그 자다. 시각 창(만든 뒤 10분)으로 재던 첫 판은 둘 다 틀렸다: 다시 보낸
    리마인더는 창 밖이라 사본이 새 기준이 됐고, 실패한 리마인더 몇 분 뒤 사람이 보낸 메일은 사본으로 버려졌다.

    **우리 영업의 이메일만 센다** — 자는 `history_view.is_sales_email` 하나(첫 회신 판정 · 답장 기준선과
    같다). 채팅(봇 답 포함) · 폼 · CS 주소의 안내(`NON_SALES_SENDER_ADDRESSES`)는 기준이 아니다 — 그 뒤에
    「지난 메일에 이어 연락드립니다」를 보내면 거짓이다(설계 §1 의 「전화 뒤 보드로 옮긴 건」과 같은 이유).
    여기에 이 시계만 **열쇠**를 더 요구한다(`_OUTSIDE_PREFIXES`) — 콘솔에 손으로 적은 메일 기록은 리마인더가
    베낄 받는 사람 · 발신 계정을 다시 찾을 길이 없다.
    """
    ids = list(conversation_ids)
    if not ids:
        return {}
    from .ticket_history import same_mail

    non_sales = non_sales_senders()

    drawn: set[str] = set()
    unkeyed: list[tuple[int, datetime, str]] = []  # 나갔을 수 있는데 사본을 알아볼 열쇠가 없는 리마인더
    for conv_id, variant, status, hub_id, smtp_id, made_at, approved_at, body in session.execute(
        select(Message.conversation_id, Message.prompt_variant, Message.status, Message.hubspot_message_id,
               Message.smtp_message_id, Message.created_at, Message.approved_at, Message.body)
        .where(Message.conversation_id.in_(ids), Message.direction == "outgoing")
    ).all():
        if hub_id:
            drawn.add(f"hubspot:conv:{hub_id}")
        if smtp_id:
            drawn.add(f"gmail:{smtp_id}")
        # 실패·거절·대기는 아무것도 안 나갔다 — 사본이 있을 수 없다.
        may_have_left = status in ("delivery_unknown", "sent") or (status or "").startswith("sending")
        if variant in REMINDER_VARIANTS and may_have_left and not hub_id and not smtp_id:
            when = approved_at or made_at
            if when is not None:
                unkeyed.append((conv_id, _naive(when), body or ""))
    out: dict[int, list[CustomerInteraction]] = {}
    for row in session.scalars(
        select(CustomerInteraction)
        .join(Conversation, Conversation.id == CustomerInteraction.conversation_id)
        .where(
            CustomerInteraction.conversation_id.in_(ids),
            # `thread_events` 처럼 그 대화의 사람 것만 — 다른 연락처 줄이 붙어 있을 수 있다.
            CustomerInteraction.contact_id == Conversation.contact_id,
            CustomerInteraction.direction.in_(("outgoing", "outbound")),
            CustomerInteraction.channel.in_(EMAIL_CHANNELS),
            CustomerInteraction.happened_at.isnot(None),
            or_(*(CustomerInteraction.external_id.like(f"{p}%") for p in _OUTSIDE_PREFIXES)),
        )
    ).all():
        if row.external_id in drawn or not is_sales_email(row, non_sales=non_sales):
            continue
        if any(c == row.conversation_id and same_mail(when, "outgoing", body, row.happened_at, "outgoing",
                                                      row.summary)
               for c, when, body in unkeyed):
            continue
        out.setdefault(row.conversation_id, []).append(row)
    return out


def _base_mailbox(base) -> str | None:
    """기준 회신이 나간 개인 사서함(`gmail:<주소>`) — 리마인더도 거기서 나가고, 끊겼으면 시계를 세운다.

    콘솔 기준은 그 행의 `channel_account_id` 그대로다. 개인함에서 직접 보낸 메일은 수집기가
    `context` 에 「<사서함> 개인 메일함」을 적어 둔다(`mailbox_sync`); 없으면 보낸 주소(`handler`).
    """
    if isinstance(base, Message):
        return base.channel_account_id
    if not (base.external_id or "").startswith("gmail:"):
        return None
    context = (base.context or "").strip()
    mailbox = context[: -len(_MAILBOX_SUFFIX)].strip() if context.endswith(_MAILBOX_SUFFIX) else ""
    mailbox = (mailbox or base.handler or "").strip().lower()
    return f"gmail:{mailbox}" if mailbox else None


def _at(row) -> datetime:
    """기준 회신이 나간 시각 — 콘솔 행은 `sent_at`, 콘솔 밖 메일은 `happened_at`."""
    return _naive(row.sent_at if isinstance(row, Message) else row.happened_at)


def _committed_at(base) -> datetime:
    """기준 회신이 **사람 손을 떠난** 시각 — 리마인더·초안이 그 뒤에 만들어졌는지 잴 자.

    콘솔 행은 승인한 시각이다. 나간 시각(`sent_at`)이 늘 그 순간은 아니다: 결과를 모른 채 끝난 발송
    (`delivery_unknown`)을 나중에 복구 화면에서 「나간 것 확인」하면 `sent_at` 이 **확인한 때**로 찍히고,
    그 사이 이미 나간 리마인더 1 이 「기준 전」이 되어 1차가 또 나갔다(2026-09-28 검토에서 재현).
    승인이 기록되지 않은 옛 행은 나간 시각으로.
    """
    if isinstance(base, Message):
        return _naive(base.approved_at or base.sent_at)
    return _at(base)


def _after_base(row: Message, base) -> bool:
    """이 행(리마인더 · 사람 초안)을 기준 회신이 **사람 손을 떠난 뒤에** 만들었나 — 만든 시각을
    `_committed_at`(콘솔 회신은 승인 시각, 콘솔 밖 메일은 보낸 시각)과 댄다.

    예전에는 id 로 쟀다(`row.id > base.id`). 기준이 다른 표(`customer_interactions`)일 수 있게 된
    뒤로는 id 를 비교할 수 없고, 콘솔 기준에서도 id 는 대리값이었다: 리마인더 1 보다 **먼저 만든**
    사람 초안을 다시 쓰고 **나중에** 보내면 그 회신이 새 기준인데 리마인더 1 이 여전히 「뒤」로 세어져,
    사흘 뒤 1차가 아니라 나흘 만에 2차(「닫겠습니다」)가 나갔다. 만든 시각이 없는 행(조립한 객체)만 id 로.
    """
    if row.created_at is not None:
        return _naive(row.created_at) > _committed_at(base)
    return isinstance(base, Message) and row.id is not None and base.id is not None and row.id > base.id


def open_draft(messages, base) -> Message | None:
    """사람이 쓰는 중인 후속 초안 — **기준 회신 뒤에 만든 것만**. 있으면 리마인더를 보류한다.

    기준 전에 만든 초안은 쓰는 중이 아니라 **버려진** 것이다: 「메일 발송」으로 열어만 두고 허브스팟
    화면에서 답했거나, 실패한 발송을 저쪽에서 다시 보냈거나, New 의 AI 초안이 안 치워졌거나. 예전에는
    그런 행 하나가 그 티켓의 리마인더를 **영원히** 막았고 화면에는 아무 표시가 없었다(2026-09-28 감사).
    """
    held = [
        m for m in messages
        if m.direction == "outgoing" and m.prompt_variant not in REMINDER_VARIANTS
        and m.status in ("drafting", "pending_approval", "approved", "send_failed")
        and _after_base(m, base)
    ]
    return max(held, key=lambda m: m.id or 0) if held else None


def sequence_state(messages, outside=()) -> tuple[Message | CustomerInteraction | None, dict[str, Message]]:
    """(기준 회신, {변형: 그 리마인더 행}). **리마인더는 기준 회신 뒤에 만든 것만** 센다.

    기준은 콘솔에서 나간 회신(`messages`)과 콘솔 밖에서 나간 우리 메일(`outside` — `outside_replies`
    의 한 대화분) 중 **늦은 것**이다. 밖의 메일이 기준이면 `CustomerInteraction` 행이 돌아온다.

    기준만 옮기고 리마인더를 그대로 세면, 사람이 후속 회신을 보낸 사흘 뒤에 「닫겠습니다」가
    나간다 — 기준은 새 회신인데 「리마인더 1 은 이미 보냈다」로 읽히기 때문이다.
    """
    base = None
    for m in messages:
        # 콘솔 회신도 같은 자(`is_sales_email` — 나간 영업 회신, 리마인더 · 옛 접수확인 아님)에, 시계가 읽을
        # 나간 시각이 있는 것만.
        if is_sales_email(m) and m.sent_at is not None:
            if base is None or _at(m) > _at(base):
                base = m
    for row in outside:
        if base is None or _at(row) > _at(base):
            base = row
    if base is None:
        return None, {}
    reminders: dict[str, Message] = {}
    for m in messages:
        if m.direction == "outgoing" and m.prompt_variant in REMINDER_VARIANTS and _after_base(m, base):
            held = reminders.get(m.prompt_variant)
            if held is None or m.id > held.id:
                reminders[m.prompt_variant] = m
    return base, reminders


def next_step(base, reminders: dict[str, Message]) -> tuple[str, datetime | None]:
    """(할 일, 그 시각). 할 일은 `send_1` · `send_2` · `close` · `sending` · `stalled`.

    **보내다 만 리마인더가 있으면 시계가 멈춘다.** `approved`·`sending` 은 워커가 곧 보낼 것이고
    (`sending`), 실패·거절은 사람이 티켓 화면에서 다시 보내거나 단계를 옮겨야 한다(`stalled`).
    그 행이 있으니 같은 리마인더를 다시 만들지 않는다 — 실패를 기계가 되풀이하면 안 된다.
    """
    for variant in REMINDER_VARIANTS:
        row = reminders.get(variant)
        if row is None or (row.status == "sent" and row.sent_at is not None):
            continue
        if row.status == "approved" or (row.status or "").startswith("sending"):
            return "sending", None
        return "stalled", None
    first, second = reminders.get(REMINDER_1), reminders.get(REMINDER_2)
    if first is None:
        return "send_1", _at(base) + AFTER_REPLY
    if second is None:
        return "send_2", _naive(first.sent_at) + AFTER_REMINDER_1
    return "close", _naive(second.sent_at) + AFTER_REMINDER_2


def view(conv: Conversation, messages, outside=()) -> dict | None:
    """티켓 화면의 한 줄 — 보드 카드도 같은 답을 읽는다(`ui_api._reminders`). 파생값이라 저장하지 않는다.

    `outside` 는 `outside_replies` 의 그 대화분이다. **안 넘기면 허브스팟 화면에서 답한 티켓에 칩이
    안 선다** — 스윕은 그 티켓에 리마인더를 보내는데 화면만 조용한 어긋남이 된다(2026-09-28).

    `reminder` 는 「몇 차까지 나갔나」를 **그릴 글자 그대로** 담는다(2026-09-22 운영자 지시:
    「리마인더 센트 기본적으로 떠있게」 — 한 통도 안 나갔으면 「Pending」). 세 갈래 전부에
    싣는다 — 닫힌·되살아난 티켓이야말로 「두 번 재촉하고 닫았다」가 적혀 있어야 하는 자리다.
    그래서 `sequence_state` 를 early return 위로 올렸다(쿼리는 안 늘어난다: `messages` 는 이미
    손에 있다).
    """
    base, reminders = sequence_state(messages, outside)
    reminder = reminder_status(messages, outside)
    closes_to = close_label()
    # 닫힌 · 되살아난 티켓의 칩은 **언제나 두 번 재촉했다**다 — 닫기는 리마인더 둘이 나간 뒤에만 일어난다.
    # 지금 기준으로 세면(`reminder_status`) 운영자가 닫힌 티켓에 메일을 한 통 보내는 순간 그 메일이 새
    # 기준이 되어 닫힌 카드에 「Pending」이 섰다(2026-09-30 검증).
    if revived_by_sequence(conv):
        return {"state": "revived", "at": conv.followup_closed_at, "reminder": done_label(REMINDER_2),
                "closes_to": closes_to}
    if closed_by_sequence(conv):
        return {"state": "closed", "at": conv.followup_closed_at, "reminder": done_label(REMINDER_2),
                "closes_to": closes_to}
    start = since()
    if start is None or conv.stage != CONTACTED:
        return None
    if base is None or _at(base) < start:
        return None
    step, due = next_step(base, reminders)
    if step == "close" and _closed_this_cycle(conv, reminders):
        # 자동 종결 뒤 사람이 Contacted 로 되돌렸다 — 새 메일이 나가기 전에는 다시 안 닫는다(스윕과 같은 판정).
        return {"state": "reopened", "at": conv.followup_closed_at, "reminder": reminder,
                "closes_to": closes_to}
    missing = None
    if step in ("send_1", "send_2"):
        # 키를 틀리게 적었거나 아직 안 만들었으면 스윕은 로그만 남기고 안 보낸다 — 화면이 그걸 말한다.
        from ..db.email_templates import get_email_template

        key = TEMPLATE_KEYS[REMINDER_1 if step == "send_1" else REMINDER_2]
        missing = None if (get_email_template(key) or "").strip() else key
    draft = open_draft(messages, base) if step in ("send_1", "send_2", "close") else None
    return {
        "state": step,
        "due": due,
        "template_missing": missing,
        # 스윕이 이 초안 때문에 기다린다 — 안 적으면 지난 날짜가 적힌 배너만 남는다.
        "held_by_draft": draft.id if draft is not None else None,
        "reminder_1_at": getattr(reminders.get(REMINDER_1), "sent_at", None),
        "reminder_2_at": getattr(reminders.get(REMINDER_2), "sent_at", None),
        "reminder": reminder,
        "closes_to": closes_to,
    }


def closed_by_sequence(conv) -> bool:
    """시퀀스가 닫았고 **그 뒤로 사람이 안 건드린** 티켓 — 고객이 연락하면 되살릴 자리.

    닫는 단계(`CLOSE_STAGE`)에 있어도, 닫은 뒤로 단계가 한 번이라도 움직였다가 돌아온 것은 아니다
    (`followup_released_at` 이 닫은 때보다 늦다 — 되살리기도, 사람이 옮긴 것도 거기 적힌다). 그건 사람의
    결정이다 — 그런 티켓을 스윕이 볼 때마다 옛 답장으로 다시 협의 중으로 돌리던 것이 2026-09-30 검증이 네
    방향에서 따로 재현한 결함이다(이관 0129).
    """
    closed, released = _naive(conv.followup_closed_at), _naive(conv.followup_released_at)
    return conv.stage == CLOSE_STAGE and closed is not None and (released is None or released < closed)


def revived_by_sequence(conv) -> bool:
    """닫았다가 고객 답장으로 되살린 티켓 — 보드와 티켓 화면에 빨갛게 선다(2026-09-17 운영자).

    사람이 손으로 협의 중에 옮긴 자동 종결 티켓은 되살아난 것이 아니다. 예전에는 「닫은 표시 + 협의 중」으로
    재서 그것까지 빨갛게 섰다.
    """
    closed, revived = _naive(conv.followup_closed_at), _naive(conv.followup_revived_at)
    return conv.stage == NEGOTIATION and closed is not None and revived is not None and revived >= closed


def _closed_this_cycle(conv, reminders: dict[str, Message]) -> bool:
    """이 회차(지금 기준 회신 뒤의 리마인더 둘)를 시퀀스가 **이미 닫은 적 있나** — 닫기는 한 회차에 한 번이다.

    사람이 자동 종결된 티켓을 Contacted 로 되돌려 놓으면(다시 열었다) 기준도 리마인더도 그대로라 `next_step` 은
    여전히 「닫을 때가 지났다」고 답한다 — 그대로 두면 다음 스윕이 10분 안에 다시 닫고 허브스팟·워크북까지
    되돌렸다(2026-09-30 검증). 새 메일을 보내면 기준이 바뀌고 리마인더가 처음부터 다시 서므로 그 회차의
    닫기는 다시 열린다.
    """
    second = reminders.get(REMINDER_2)
    closed = _naive(conv.followup_closed_at)
    return (closed is not None and second is not None and second.sent_at is not None
            and closed >= _naive(second.sent_at))


def close_label() -> str:
    """닫는 단계의 **화면 이름** — 배너가 「… 이후 <이름> 로 닫습니다」에 쓴다.

    이름의 출처는 `customer_ops.PIPELINE_STAGES` 한 곳이다(CLAUDE.md). 배너가 「Closed Lost」를 글자로
    들고 있었고, 그래서 닫는 단계를 바꾸는 일이 화면 두 줄을 따로 고치는 일이었다 — 허브스팟이 이름을
    바꾸면(그 단계는 이미 네 번 바뀌었다) 또 그렇다.
    """
    from ..api.routes.customer_ops import PIPELINE_STAGES

    return next((label for key, label, _ in PIPELINE_STAGES if key == CLOSE_STAGE), CLOSE_STAGE)


def _replies(session, contact_ids: set[int], after: datetime,
             own=()) -> list[tuple[int, int | None, datetime]]:
    """(연락처, 붙은 대화, 시각) — `after` 뒤에 고객이 연락한 것 전부.

    `own` 은 지금 재촉할지 보는 대화들이다. **그 대화의 첫 문의 행은 답장이 아니다** — 그 행의 시각은
    고객이 쓴 때가 아니라 우리가 접수한 때(`created_at`)라, 허브스팟 화면에서 먼저 답하고 접수가 몇 분
    늦으면(잠든 서버 · 놓친 웹훅을 10분 폴러가 줍는 경우) 문의 자체가 「기준 뒤 고객 연락」이 되어
    Negotiating 으로 옮겨졌다(2026-09-28 검토에서 재현). 콘솔 회신이 기준일 때는 늘 접수 뒤라 안 걸렸다.
    다른 대화의 첫 문의(같은 사람이 새 폼을 냈다)는 그대로 연락이다.

    **연락처 단위로 본다.** 답장이 다른 대화에 붙는 길이 셋이다 — 개인함 메일은 그 고객의
    최신 대화에 붙고, 새 폼·채팅은 새 티켓이고, 옛 스레드에 답하면 옛 대화다. 대화 단위로만
    보면 방금 답한 고객에게 리마인더가 간다.

    출처는 가리지 않는다: 허브스팟 스레드 · 개인 사서함 · 콘솔에 사람이 적은 「수신」 기록.
    옛 수집기는 `incoming` 으로 적었다.
    """
    if not contact_ids:
        return []
    found = [
        (row.contact_id, row.conversation_id, row.happened_at)
        for row in session.execute(
            select(CustomerInteraction.contact_id, CustomerInteraction.conversation_id,
                   CustomerInteraction.happened_at)
            .where(CustomerInteraction.contact_id.in_(contact_ids),
                   CustomerInteraction.direction.in_(("inbound", "incoming")),
                   CustomerInteraction.happened_at > after)
        ).all()
    ]
    first_inquiries = (
        select(func.min(Message.id))
        .where(Message.conversation_id.in_(list(own)), Message.direction == "inbound")
        .group_by(Message.conversation_id)
    )
    found += [
        (row.contact_id, row.conversation_id, row.created_at)
        for row in session.execute(
            select(Conversation.contact_id, Message.conversation_id, Message.created_at)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.contact_id.in_(contact_ids),
                   Message.direction == "inbound",
                   Message.created_at > after,
                   Message.id.not_in(first_inquiries))
        ).all()
    ]
    return found


def _reply_place(replies, contact_id: int, conversation_id: int, after: datetime) -> str | None:
    """`here`(이 티켓) · `elsewhere`(같은 사람의 다른 자리) · None."""
    places = {conv for who, conv, at in replies if who == contact_id and _naive(at) > after}
    if not places:
        return None
    return "here" if conversation_id in places else "elsewhere"


def _reminder_body(variant: str, target: str) -> str | None:
    """콘솔 템플릿을 고객 언어로. 못 만들면 None — **행을 만들기 전에** 가른다.

    그 언어로 쓴 행(`<키>_<언어>` — `followup_reminder_ko` · `followup_closing_ja`)이 있으면 **쓴 그대로**
    나간다: 토큰만 채우고 번역하지 않는다(2026-10-07 운영자: 「리마인더는 써있는거 그대로 보내져야한다」 ·
    「1번으로 해서 그 언어에 맞게 번역하도록」). 없으면 접미사 없는 영문 행이고, 고객 언어가 영어가 아니면
    그것을 번역한다.

    한국어 본문이 영어 고객에게, 영문이 한국어 고객에게 가는 것을 여기서 막는다. 발송 경로의
    `enforce_send_language` 는 두 번째 그물이고, 거기서 걸리면 `send_failed` 가 되어 시퀀스가 멈춘다.
    """
    from ..db.email_templates import get_email_template
    from ..llm.prompts import apply_editable_tokens
    from ..llm.translate import is_mostly_korean, translate_to

    key = TEMPLATE_KEYS[variant]
    written = (get_email_template(f"{key}_{target.replace('-', '_')}") or "").strip()
    body = written or (get_email_template(key) or "").strip()
    if not body:
        logger.warning("후속 리마인더 템플릿 「%s」 이 콘솔에 없어 보내지 않습니다.", key)
        return None
    body = apply_editable_tokens(body, target)
    if not written and not target.startswith("en"):
        body = translate_to(body, target)
        if not body:
            logger.warning("후속 리마인더를 %s 로 번역하지 못해 이번 회차는 건너뜁니다.", target)
            return None
    if (target == "ko") != is_mostly_korean(body):
        logger.warning("후속 리마인더 본문의 언어가 %s 가 아니라 보내지 않습니다.", target)
        return None
    return body


def _due_now(messages, base, reminders, wanted: str) -> bool:
    """**행동 직전에 새로 읽은 행으로** 그 단계가 아직 할 일인가 — 리마인더를 만들 때와 닫을 때 둘 다.

    스윕은 회차 앞에서 읽은 것으로 판단하는데, 그 뒤 `_recheck` 가 허브스팟에서 **방금 보낸 사람 회신**을
    가져올 수 있다(웹훅 유실, 수집 대기열이 밀렸거나, 같은 회차의 수집 단계 뒤에 보낸 메일). 그러면 기준이
    그 회신이다. 다시 안 재면 고객이 사람 메일 몇 분 뒤 「지난 메일에 이어」를 받거나, 우리가 방금 메일을
    보낸 티켓이 종결로 닫힌다(2026-09-28 검토에서 둘 다 재현).
    """
    step, due = next_step(base, reminders)
    return step == wanted and due is not None and _utcnow() >= due and open_draft(messages, base) is None


def _still_due(conversation_id: int, wanted: str) -> bool:
    with SessionLocal() as session:
        messages = session.scalars(
            select(Message).where(Message.conversation_id == conversation_id)
        ).all()
        outside = outside_replies(session, [conversation_id]).get(conversation_id, [])
        base, reminders = sequence_state(messages, outside)
    return base is not None and _due_now(messages, base, reminders, wanted)


async def _hubspot_reply(ticket_id: str, message_id: str) -> dict | None:
    """허브스팟 화면에서 보낸 그 회신의 받는 사람 · 참조 · 발신 계정 · 제목. 못 찾으면 None. 읽기만 한다.

    수집기는 이 넷을 저장하지 않는다(`ticket_history.collect_ticket_history`) — 그래서 리마인더를 만들기
    직전에 그 메시지를 한 번 더 읽는다. 스레드 목록과 메시지 읽기는 수집기의 함수를 그대로 쓴다.
    """
    from ..integrations.hubspot import HubSpotClient
    from .ticket_history import _addresses, _live_thread_ids, _thread_messages

    client = HubSpotClient()
    try:
        for thread_id in await _live_thread_ids(client, ticket_id):
            for message in await _thread_messages(client, thread_id):
                if str(message.get("id") or "") != message_id:
                    continue
                recipients = message.get("recipients") or []
                return {
                    "to": _addresses([r for r in recipients if (r or {}).get("recipientField") == "TO"]),
                    "cc": _addresses([r for r in recipients if (r or {}).get("recipientField") == "CC"]),
                    "account": str(message.get("channelAccountId") or "").strip() or None,
                    "subject": (str(message.get("subject") or "").strip()[:300] or None),
                    # 우리 인박스 주소들(한 시간 캐시) — 참조에서 뺀다. 동료가 인박스 주소를 TO 에 넣고
                    # 전체 회신한 메일이 기준이면, 안 빼면 고객이 받는 리마인더의 참조에 보낸 주소 자신이 선다.
                    "inboxes": {
                        str((account.get("deliveryIdentifier") or {}).get("value") or "").strip().lower()
                        for account in await client._live_email_channel_accounts()
                    },
                }
    finally:
        await client.close()
    return None


def _outside_copy(conv: Conversation, contact_email: str | None, base: CustomerInteraction) -> dict | None:
    """콘솔 밖 메일이 기준일 때 리마인더가 베낄 것. **같은 사람이 같은 문으로** 이어 쓰게 한다.

    - 허브스팟 화면 회신이면 그 메시지의 받는 사람 · 참조 · 발신 계정 · 제목(`_hubspot_reply`).
      못 읽으면 None — 이번 회차는 안 보낸다(`_recheck` 와 같은 fail closed). 참조를 잃은 채
      나가는 것보다 10분 늦는 편이 낫다.
    - 개인 사서함 메일이면 **그 사서함에서** 나간다(콘솔 기준의 `gmail:` 과 같은 운영자 결정 ④).
      받는 사람은 그 고객, 참조는 수집기가 안 적어서 없다.
    - **언어는 그 회신 본문의 언어다** — 콘솔 기준의 `target_language`(운영자가 쓴 언어)와 같은 뜻.
      `inquiry_language` 로 두면 틀린다: 폼 문의는 본문 앞에 영어 칸 이름(`Work email:` …)이 붙어
      포르투갈어 문의가 `en` 으로 잡힐 수 있고(424), 운영자가 포르투갈어로 답한 고객에게 영어가 간다.
    - **서명 카드는 안 붙인다**(NULL = 「서명 없음」). 허브스팟 화면 회신은 본문에 손으로 쓴 맺음말뿐
      이었다(424·425 실측) — 그대로 베낀 것이다. 콘솔의 기본 서명(목록의 첫 카드)은 다른 사람이나
      한국어 카드일 수 있어서, 남의 이름으로 재촉하는 것보다 카드 없이 나가는 쪽이 낫다.
    - 제목은 그 회신 제목에 「RE:」 하나 — 고객의 메일함에서 같은 대화로 묶이게. 제목을 못 찾으면 나갈 언어의
      기본 제목이다 — 허브스팟 티켓 이름(`inquiry_subject`)은 CS 가 붙인 내부 이름일 수 있어 쓰지 않는다.
    - **받는 사람은 언제나 그 고객이다.** 수집기는 티켓 스레드의 모든 메시지를 그 고객 줄로 넣고 방향만
      보낸 주소로 가르므로, 허브스팟 화면에서 동료·파트너에게 **전달한** 메일도 「우리가 보낸 이메일」로
      선다. 그 메일의 TO 를 베끼면 「지난 메일에 이어」와 「닫겠습니다」가 그 사람에게 간다. 그래서 고객이
      TO·CC 어디에도 없는 메일이면 안 보내고, 고객 말고 TO 에 있던 사람은 참조로 옮겨 회신 전체의
      독자를 지킨다.
    """
    from ..llm.language import detect_language

    text = (base.summary or "").strip()
    if text and text != "(본문 없음)":
        # 판정 실패를 「영어」로 읽지 않는다 — 스페인어로 답한 고객에게 영어가 간다. 번역 실패와 같이
        # 이번 회차를 건너뛰고 다음 회차가 다시 잰다.
        detected = detect_language(text, default=None)
        if not detected:
            logger.warning("문의 %s: 기준 회신의 언어를 못 가려 이번 회차는 건너뜁니다.", conv.id)
            return None
        target = detected
    else:
        target = conv.inquiry_language or "en"
    target = target.strip().lower() or "en"

    def subject(found: str | None = None) -> str:
        return choose_reply_subject(thread_subject=found or base.subject, target=target)[0]

    external_id = base.external_id or ""
    if external_id.startswith("gmail:"):
        account = _base_mailbox(base)
        if not account or not contact_email:
            return None
        return {"to_address": contact_email, "cc_addresses": None, "channel_account_id": account,
                "subject": subject(), "signature_key": None, "target": target}
    ticket_id = (conv.hubspot_ticket_id or "").strip()
    if not ticket_id:
        return None
    try:
        found = asyncio.run(_hubspot_reply(ticket_id, external_id.removeprefix("hubspot:conv:")))
    except Exception:
        logger.warning("문의 %s: 허브스팟에서 보낸 회신을 못 읽어 이번 회차는 건너뜁니다.", conv.id,
                       exc_info=True)
        return None
    if found is None:
        logger.warning("문의 %s: 기준 회신이 허브스팟 스레드에 없어 이번 회차는 건너뜁니다.", conv.id)
        return None
    customer = (contact_email or "").strip().lower()
    audience = found["to"] + found["cc"]
    # ponytail: 전달 메일은 여기서만 알아본다 — 수집기가 받는 사람을 안 적어 두므로 `outside_replies` 는 그것을
    # 기준으로 세고, 그 티켓은 다음에 고객에게 메일이 나갈 때까지 조용히 멈춘다(엉뚱한 사람을 재촉하는 것보다
    # 낫다). 실제로 자주 나면 수집기가 받는 사람을 저장하게 하고 거기서 거른다.
    if not customer or customer not in audience:
        logger.warning("문의 %s: 기준 회신이 고객에게 간 메일이 아니라(전달 등) 리마인더를 보내지 않습니다.",
                       conv.id)
        return None
    cc = list(dict.fromkeys(a for a in audience if a != customer and a not in found["inboxes"]))
    return {"to_address": contact_email, "cc_addresses": ", ".join(cc) or None,
            "channel_account_id": found["account"], "subject": subject(found["subject"]),
            "signature_key": None, "target": target}


def _create_reminder(conversation_id: int, variant: str) -> int | None:
    """기준 회신을 베낀 `approved` 행 하나. 발송은 워커가 한다.

    서명 · CC · 발신 계정(개인 사서함이면 그 사서함) · 제목 · 받는 사람이 전부 기준 회신의 것이다 —
    같은 사람이 같은 문으로 이어 쓰는 메일이다. 서명의 NULL 은 「서명 없음으로 골랐다」라 그대로 둔다.
    기준이 콘솔 밖 메일이면 베낄 것을 `_outside_copy` 가 찾는다.
    """
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        messages = session.scalars(
            select(Message).where(Message.conversation_id == conversation_id)
        ).all()
        outside = outside_replies(session, [conversation_id]).get(conversation_id, [])
        base, reminders = sequence_state(messages, outside)
        if conv is None or base is None:
            return None
        if not _due_now(messages, base, reminders, "send_1" if variant == REMINDER_1 else "send_2"):
            logger.info("문의 %s: 보내기 직전에 기준이 바뀌어 %s 를 이번 회차에 만들지 않습니다.",
                        conversation_id, variant)
            return None
        contact = session.get(Contact, conv.contact_id) if conv.contact_id else None
        session.expunge_all()
    if isinstance(base, Message):
        target = ((base.target_language or conv.inquiry_language or "en").strip().lower()) or "en"
        copy = {
            "to_address": base.to_address, "cc_addresses": base.cc_addresses,
            "channel_account_id": base.channel_account_id,
            # 기준 회신의 제목에 RE: 하나 — 첫 회신이 새 제목(RE: 없이)으로 나갔어도 리마인더는 그 스레드에 붙습니다.
            "subject": choose_reply_subject(thread_subject=base.subject, target=target)[0],
            "signature_key": base.signature_key,
            "target": target,
        }
    else:
        # 허브스팟을 읽는 동안 세션을 안 붙든다 — 저쪽이 느린 날 연결이 그만큼 묶인다.
        copy = _outside_copy(conv, contact.email if contact else None, base)
        if copy is None:
            return None
    target = copy["target"]
    body = _reminder_body(variant, target)
    if body is None:
        return None
    wanted = "send_1" if variant == REMINDER_1 else "send_2"
    with SessionLocal() as session:
        # **넣기 직전에 대화 행을 잠그고 한 번 더 잽니다** (2026-09-29 감사). 배포가 겹쳐 폴러 둘이 같은 분에
        # 돌면 둘 다 위의 확인을 지나고, 그 사이에는 허브스팟 읽기(`_outside_copy`)까지 있습니다. 잠금은 먼저
        # 온 쪽의 커밋까지 기다리므로 뒤에 온 쪽은 방금 생긴 리마인더를 보고 물러섭니다. SQLite 에는 행
        # 잠금이 없지만 거기는 프로세스가 하나입니다.
        session.get(Conversation, conversation_id, with_for_update=True)
        fresh = session.scalars(select(Message).where(Message.conversation_id == conversation_id)).all()
        base_now, reminders_now = sequence_state(
            fresh, outside_replies(session, [conversation_id]).get(conversation_id, []))
        if base_now is None or not _due_now(fresh, base_now, reminders_now, wanted):
            logger.info("문의 %s: 넣기 직전에 다른 회차가 먼저 만들어 %s 를 건너뜁니다.", conversation_id, variant)
            return None
        now = _utcnow()
        row = Message(
            conversation_id=conversation_id,
            direction="outgoing",
            channel="email",
            to_address=copy["to_address"],
            subject=copy["subject"],
            body=body,
            language=target,
            target_language=target,
            status="approved",
            signature_key=copy["signature_key"],
            channel_account_id=copy["channel_account_id"],
            cc_addresses=copy["cc_addresses"],
            prompt_variant=variant,
            approved_by=APPROVER,
            approved_at=now,
            scheduled_at=now,
        )
        session.add(row)
        session.commit()
        message_id = row.id
    logger.info("문의 %s: 후속 %s 를 발송 대기에 올렸습니다 (msg=%s).", conversation_id, variant, message_id)
    if not settings.SEND_WORKER_ENABLED:
        # 워커가 켜진 운영에서는 워커가 집는다. 여기서도 보내면 두 길이 같은 메일을 다툰다.
        from .send_worker import send_approved_now

        asyncio.run(send_approved_now(message_id))
    return message_id


def _close(conversation_id: int) -> None:
    """종결(`CLOSE_STAGE`) — 콘솔 보드가 카드를 옮길 때와 같은 두 함수. 허브스팟·워크북까지 간다."""
    from ..api.routes.customer_ops import _set_conversation_stage, _sync_stage

    ticket_id, contact_id, sheet_client_id = _set_conversation_stage(conversation_id, CLOSE_STAGE)
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        if conv is not None:
            conv.followup_closed_at = _utcnow()
            # 두 번째 회차의 닫기는 위 단계 이동이 옛 닫기에 대한 「움직였다」를 방금 적었다 — 새 닫기는 새로 센다.
            conv.followup_released_at = None
            session.commit()
    asyncio.run(_sync_stage(ticket_id, CLOSE_STAGE, contact_id, sheet_client_id))
    logger.info("문의 %s: 답이 없어 후속 리마인더가 %s 로 닫았습니다.", conversation_id, close_label())


def _advance(conversation_id: int, contact_id: int) -> None:
    """고객이 연락했다 → Negotiating. 수집기(`ticket_history`)가 쓰는 그 함수다."""
    from .ticket_history import _advance_on_customer_reply

    asyncio.run(_advance_on_customer_reply(conversation_id, contact_id))


def _mark_revived(conversation_id: int) -> None:
    """되살렸다는 빨간 표시(이관 0129). **옮겨졌을 때만** 적는다 — `_advance` 는 실패를 삼키므로(단계 이동이
    허브스팟에서 실패해도 수집은 성공이다) 여기서 한 번 더 본다."""
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        if conv is not None and conv.stage == NEGOTIATION:
            conv.followup_revived_at = _utcnow()
            session.commit()


def _dead_mailboxes(session) -> set[str]:
    """토큰이 죽은 개인 사서함. 그리로 온 답장은 며칠이고 안 들어오는데 시계만 돈다."""
    return {
        f"gmail:{(email or '').lower()}"
        for email in session.scalars(
            select(MailboxAccount.email).where(
                MailboxAccount.enabled.is_(True), MailboxAccount.last_error.isnot(None)
            )
        ).all()
    }


def _recheck(conversation_id: int, contact_id: int, after: datetime) -> bool:
    """보내거나 닫기 **직전에** 허브스팟 스레드를 한 번 더 읽는다. 계속해도 되면 True.

    웹훅이 유실됐거나 잠든 사이 온 답장에 「닫겠습니다」를 보내는 것이 이 기능의 최악이다.
    읽기가 실패하면 **안 보낸다**(fail closed) — 다음 회차가 다시 본다.
    """
    from . import ticket_history

    try:
        asyncio.run(ticket_history.sync_one_ticket(conversation_id))
    except Exception:
        logger.warning("문의 %s: 보내기 전 확인을 못 해 이번 회차는 건너뜁니다.", conversation_id,
                       exc_info=True)
        return False
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        if conv is None or conv.stage != CONTACTED:
            return False  # 수집기가 이미 옮겼다
        replies = _replies(session, {contact_id}, after, own={conversation_id})
    place = _reply_place(replies, contact_id, conversation_id, after)
    if place == "here":
        _advance(conversation_id, contact_id)
    return place is None


def run_followup_sequence_once(limit: int = PER_SWEEP) -> dict:
    """10분 폴러의 한 단계(**동기** 함수 — 폴러가 스레드로 돌린다)."""
    from ..common.safe_mode import email_delivery_enabled

    start = since()
    if start is None:
        return {}
    now = _utcnow()
    with SessionLocal() as session:
        convs = session.scalars(
            select(Conversation).where(
                Conversation.hubspot_ticket_id.isnot(None),
                (Conversation.stage == CONTACTED)
                # 시퀀스가 닫았고 그 뒤로 단계가 안 움직인 것만(`closed_by_sequence` 와 같은 판정 — 이관 0129).
                | ((Conversation.stage == CLOSE_STAGE) & Conversation.followup_closed_at.isnot(None)
                   & (Conversation.followup_released_at.is_(None)
                      | (Conversation.followup_released_at < Conversation.followup_closed_at))),
                # SINCE 뒤에 우리 메일이 나간 대화만 — 콘솔에서든(`messages`) 허브스팟 화면·개인
                # 사서함에서든(`customer_interactions`). `last_outgoing_at` 으로 좁히면 싸지만 그
                # 칸을 안 채우는 발송 경로가 있다(`approval.mark_sent`, 그리고 콘솔 밖 회신 전부).
                Conversation.id.in_(
                    select(Message.conversation_id).where(
                        Message.direction == "outgoing", Message.status == "sent",
                        Message.sent_at >= start,
                    )
                )
                | Conversation.id.in_(
                    select(CustomerInteraction.conversation_id).where(
                        # 후보만 고른다 — 「영업 메일인가」는 `outside_replies` 가 같은 자로 다시 잰다.
                        CustomerInteraction.direction.in_(("outgoing", "outbound")),
                        CustomerInteraction.channel.in_(EMAIL_CHANNELS),
                        CustomerInteraction.happened_at >= start,
                        or_(*(CustomerInteraction.external_id.like(f"{p}%") for p in _OUTSIDE_PREFIXES)),
                    )
                ),
            )
        ).all()
        if not convs:
            return {}
        by_conv: dict[int, list[Message]] = {}
        for m in session.scalars(
            select(Message).where(Message.conversation_id.in_([c.id for c in convs]),
                                  Message.direction == "outgoing")
        ).all():
            by_conv.setdefault(m.conversation_id, []).append(m)
        outside = outside_replies(session, [c.id for c in convs])
        replies = _replies(session, {c.contact_id for c in convs}, start, own=[c.id for c in convs])
        dead = _dead_mailboxes(session)
        session.expunge_all()

    delivery_on = email_delivery_enabled()
    window = in_send_window(now)
    done = {"advanced": 0, "sent": 0, "closed": 0}
    acted = 0
    for conv in convs:
        if acted >= limit:
            break
        try:
            outgoing = by_conv.get(conv.id, [])
            base, reminders = sequence_state(outgoing, outside.get(conv.id, ()))
            if base is None or _at(base) < start:
                continue
            after = _at(base)
            revive = closed_by_sequence(conv)
            if revive:
                # **닫은 뒤의 연락은 전부 되살린다**(설계). 기준만 보면 고객 답장과 운영자의 허브스팟 화면
                # 회신이 같은 회차에 들어올 때 운영자 회신이 새 기준이 되어 그 앞의 고객 답장이 안 보이고,
                # 티켓은 종결에 남는다 — 되살리는 길은 이 스윕 하나뿐이다(2026-09-28 검토에서 재현).
                after = min(after, _naive(conv.followup_closed_at))
            place = _reply_place(replies, conv.contact_id, conv.id, after)
            if place == "here":
                _advance(conv.id, conv.contact_id)
                if revive:
                    _mark_revived(conv.id)
                done["advanced"] += 1
                acted += 1
                continue
            # 같은 사람이 다른 자리에서 연락했다 — 옮기지는 않되(어느 대화가 살아 있는지는
            # 사람이 안다) 이 티켓으로 재촉하지도 않는다.
            if place == "elsewhere" or conv.stage != CONTACTED:
                continue
            step, due = next_step(base, reminders)
            if due is None or now < due:
                continue
            if step == "close" and _closed_this_cycle(conv, reminders):
                continue  # 사람이 자동 종결을 되돌려 다시 열었다 — 새 메일이 나가기 전엔 다시 안 닫는다
            # 비상 스위치를 내렸는데 시계가 돌면, 한 통도 못 받은 고객의 문의가 종결된다.
            if not delivery_on or not window:
                continue
            if (_base_mailbox(base) or "").lower() in dead:
                continue
            if open_draft(outgoing, base) is not None:
                continue  # 사람이 쓰는 중이다 — 몇 분 사이 두 통을 받게 하지 않는다
            acted += 1
            if not _recheck(conv.id, conv.contact_id, after):
                continue
            if step == "close":
                if not _still_due(conv.id, "close"):
                    logger.info("문의 %s: 닫기 직전에 기준이 바뀌어 이번 회차에 닫지 않습니다.", conv.id)
                    continue
                _close(conv.id)
                done["closed"] += 1
            elif _create_reminder(conv.id, REMINDER_1 if step == "send_1" else REMINDER_2):
                done["sent"] += 1
        except Exception:
            logger.warning("후속 리마인더 처리 실패 (conversation=%s)", conv.id, exc_info=True)
    if any(done.values()):
        logger.info("후속 리마인더: 협의 중 %(advanced)d · 발송 %(sent)d · 종료 %(closed)d", done)
    return done
