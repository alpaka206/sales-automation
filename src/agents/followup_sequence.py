"""Contacted 후속 리마인더 — 답이 없으면 3일 · 5일 · 7일 (2026-09-17 운영자 지시).

허브스팟 워크플로(`4623059693`)가 하던 일을 옮겨 왔다. 설계와 근거는
`docs/후속-회신-시퀀스-설계.md` — 초안을 세 방향에서 코드에 맞대어 깨뜨린 뒤의 판이다.

| 기준 | 답이 없으면 |
|---|---|
| 우리 회신 +3일 | 리마인더 1 (템플릿 `followup_reminder`) |
| 리마인더 1 +5일 | 리마인더 2 (템플릿 `followup_closing`) |
| 리마인더 2 +7일 | Closed Lost |

그 사이 언제든 고객이 연락하면 Negotiating 으로 옮기고 끝. **닫은 뒤에** 연락이 와도
Negotiating 으로 되살리고, 그 티켓은 화면에 빨갛게 선다(`followup_closed_at`).

**새 표도 새 발송 경로도 없다.**
- 리마인더는 `messages` 행이다. 몇 번째를 보냈는지는 그 행들에서 읽는다(`sequence_state`) —
  저장하면 원본이 바뀔 때 조용히 어긋난다.
- 행을 `approved` 로 세우면 켜져 있는 발송 워커가 보낸다. 그래서 안전 관문 · 스레드 고르기 ·
  발신 주소(개인 사서함 포함) · CC · 서명 · 실패 사유가 사람이 누른 발송과 한 글자도 다르지
  않다. **「모든 회신은 사람이 승인한다」의 유일한 예외**이고, 승인에 해당하는 것은 운영자가
  콘솔에 템플릿을 쓴 행위다.
- 시계의 기준은 **이 콘솔에서 나간 마지막 회신**이다. 단계 이동 시각은 기록하지 않고(2026-08-20
  지시), Contacted 는 발송 워커가 회신을 보낸 그 자리에서 올리므로 같은 사건이다. 운영자가 후속
  회신을 또 보내면 기준이 그리로 옮겨 가고 3·5·7 이 처음부터 다시 간다.

`FOLLOWUP_SEQUENCE_SINCE` 가 비어 있으면 아무것도 안 한다. 그 날짜 뒤에 나간 회신만 시계를
돈다 — 기존 티켓은 안 건드린다는 지시가 그 한 칸이다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..common.config import settings
from ..db.models import Conversation, CustomerInteraction, MailboxAccount, Message
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

REMINDER_1 = "followup_reminder_1"
REMINDER_2 = "followup_reminder_2"
REMINDER_VARIANTS = (REMINDER_1, REMINDER_2)
REMINDER_ORDINAL = {REMINDER_1: "1차", REMINDER_2: "2차"}
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
CLOSED_LOST = "closed_lost"

AFTER_REPLY = timedelta(days=3)
AFTER_REMINDER_1 = timedelta(days=5)
AFTER_REMINDER_2 = timedelta(days=7)
# 한 회차에 보내거나 닫는 수. 읽기는 대상 수와 무관하게 쿼리 넷이다.
PER_SWEEP = 20
_KST = timezone(timedelta(hours=9))


def done_label(variant: str) -> str:
    """「1차 리마인더 완료」 — 티켓 배너 · 소통 히스토리 · 진행 기록이 **같은 한 문장**을 적는다.

    변형 이름을 이 모듈이 들고 있으니 그 말도 여기가 들고 있다(2026-09-21 운영자 지시:
    「1차 리마인더 완료 이런식으로」). 두 곳에 적으면 세 화면이 같은 사건을 다르게 부른다.
    """
    return f"{REMINDER_ORDINAL[variant]} 리마인더 완료"


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


def sequence_state(messages) -> tuple[Message | None, dict[str, Message]]:
    """(기준 회신, {변형: 그 리마인더 행}). **리마인더는 기준 회신 뒤에 만든 것만** 센다.

    기준만 옮기고 리마인더를 그대로 세면, 사람이 후속 회신을 보낸 사흘 뒤에 「닫겠습니다」가
    나간다 — 기준은 새 회신인데 「리마인더 1 은 이미 보냈다」로 읽히기 때문이다.
    """
    base = None
    for m in messages:
        if (m.direction == "outgoing" and m.status == "sent" and m.sent_at is not None
                and m.prompt_variant not in REMINDER_VARIANTS):
            if base is None or _naive(m.sent_at) > _naive(base.sent_at):
                base = m
    if base is None:
        return None, {}
    reminders: dict[str, Message] = {}
    for m in messages:
        if m.direction == "outgoing" and m.prompt_variant in REMINDER_VARIANTS and m.id > base.id:
            held = reminders.get(m.prompt_variant)
            if held is None or m.id > held.id:
                reminders[m.prompt_variant] = m
    return base, reminders


def next_step(base: Message, reminders: dict[str, Message]) -> tuple[str, datetime | None]:
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
        return "send_1", _naive(base.sent_at) + AFTER_REPLY
    if second is None:
        return "send_2", _naive(first.sent_at) + AFTER_REMINDER_1
    return "close", _naive(second.sent_at) + AFTER_REMINDER_2


def view(conv: Conversation, messages) -> dict | None:
    """티켓 화면의 한 줄. 파생값이라 저장하지 않는다.

    `done` 은 「몇 차까지 나갔나」를 **그릴 글자 그대로** 담는다(2026-09-21 운영자 지시).
    세 갈래 전부에 싣는다 — 닫힌·되살아난 티켓이야말로 「두 번 재촉하고 닫았다」가 적혀
    있어야 하는 자리다. 그래서 `sequence_state` 를 early return 위로 올렸다(쿼리는 안 늘어난다:
    `messages` 는 이미 손에 있다).
    """
    base, reminders = sequence_state(messages)
    # 판정은 `next_step` 이 「이미 보냈다」로 쓰는 그 조건이다. `sent_at` 만 보면 `test_sent`
    # (SAFE 모드로 나간 것 — 고객이 받은 것이 없다)가 완료로 읽히고, 시퀀스는 `stalled` 인데
    # 화면만 완료라고 적는다.
    done = [
        done_label(variant)
        for variant in REMINDER_VARIANTS
        if (row := reminders.get(variant)) is not None
        and row.status == "sent" and row.sent_at is not None
    ]
    if conv.followup_closed_at is not None and conv.stage == NEGOTIATION:
        return {"state": "revived", "at": conv.followup_closed_at, "done": done}
    if conv.followup_closed_at is not None and conv.stage == CLOSED_LOST:
        return {"state": "closed", "at": conv.followup_closed_at, "done": done}
    start = since()
    if start is None or conv.stage != CONTACTED:
        return None
    if base is None or _naive(base.sent_at) < start:
        return None
    step, due = next_step(base, reminders)
    missing = None
    if step in ("send_1", "send_2"):
        # 키를 틀리게 적었거나 아직 안 만들었으면 스윕은 로그만 남기고 안 보낸다 — 화면이 그걸 말한다.
        from ..db.email_templates import get_email_template

        key = TEMPLATE_KEYS[REMINDER_1 if step == "send_1" else REMINDER_2]
        missing = None if (get_email_template(key) or "").strip() else key
    return {
        "state": step,
        "due": due,
        "template_missing": missing,
        "reminder_1_at": getattr(reminders.get(REMINDER_1), "sent_at", None),
        "reminder_2_at": getattr(reminders.get(REMINDER_2), "sent_at", None),
        "done": done,
    }


def _replies(session, contact_ids: set[int], after: datetime) -> list[tuple[int, int | None, datetime]]:
    """(연락처, 붙은 대화, 시각) — `after` 뒤에 고객이 연락한 것 전부.

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
    found += [
        (row.contact_id, row.conversation_id, row.created_at)
        for row in session.execute(
            select(Conversation.contact_id, Message.conversation_id, Message.created_at)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.contact_id.in_(contact_ids),
                   Message.direction == "inbound",
                   Message.created_at > after)
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
    """콘솔의 영문 템플릿을 고객 언어로. 못 만들면 None — **행을 만들기 전에** 가른다.

    한국어 본문이 영어 고객에게, 영문이 한국어 고객에게 가는 것을 여기서 막는다. 발송 경로의
    `enforce_send_language` 는 두 번째 그물이고, 거기서 걸리면 `send_failed` 가 되어 시퀀스가 멈춘다.
    """
    from ..db.email_templates import get_email_template
    from ..llm.prompts import apply_editable_tokens
    from ..llm.translate import is_mostly_korean, translate_to

    key = TEMPLATE_KEYS[variant]
    body = (get_email_template(key) or "").strip()
    if not body:
        logger.warning("후속 리마인더 템플릿 「%s」 이 콘솔에 없어 보내지 않습니다.", key)
        return None
    body = apply_editable_tokens(body, target)
    if not target.startswith("en"):
        body = translate_to(body, target)
        if not body:
            logger.warning("후속 리마인더를 %s 로 번역하지 못해 이번 회차는 건너뜁니다.", target)
            return None
    if (target == "ko") != is_mostly_korean(body):
        logger.warning("후속 리마인더 본문의 언어가 %s 가 아니라 보내지 않습니다.", target)
        return None
    return body


def _create_reminder(conversation_id: int, variant: str) -> int | None:
    """기준 회신을 베낀 `approved` 행 하나. 발송은 워커가 한다.

    서명 · CC · 발신 계정(개인 사서함이면 그 사서함) · 제목 · 받는 사람이 전부 기준 회신의 것이다 —
    같은 사람이 같은 문으로 이어 쓰는 메일이다. 서명의 NULL 은 「서명 없음으로 골랐다」라 그대로 둔다.
    """
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        messages = session.scalars(
            select(Message).where(Message.conversation_id == conversation_id)
        ).all()
        base, _ = sequence_state(messages)
        if conv is None or base is None:
            return None
        target = ((base.target_language or conv.inquiry_language or "en").strip().lower()) or "en"
        body = _reminder_body(variant, target)
        if body is None:
            return None
        now = _utcnow()
        row = Message(
            conversation_id=conversation_id,
            direction="outgoing",
            channel="email",
            to_address=base.to_address,
            subject=base.subject,
            body=body,
            language=target,
            target_language=target,
            status="approved",
            signature_key=base.signature_key,
            channel_account_id=base.channel_account_id,
            cc_addresses=base.cc_addresses,
            prompt_variant=variant,
            approved_by=APPROVER,
            approved_at=now,
            scheduled_at=now,
        )
        # ponytail: 「이미 만들었나」는 이 스윕이 읽은 행으로만 가른다. 배포가 겹쳐 폴러 둘이 같은
        # 분에 돌면 둘 다 만들 수 있다 — 실제로 한 번이라도 나면 (기준 회신, 변형) 유니크를 건다.
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
    """Closed Lost — 콘솔 보드가 카드를 옮길 때와 같은 두 함수. 허브스팟·워크북까지 간다."""
    from ..api.routes.customer_ops import _set_conversation_stage, _sync_stage

    ticket_id, contact_id, sheet_client_id = _set_conversation_stage(conversation_id, CLOSED_LOST)
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        if conv is not None:
            conv.followup_closed_at = _utcnow()
            session.commit()
    asyncio.run(_sync_stage(ticket_id, CLOSED_LOST, contact_id, sheet_client_id))
    logger.info("문의 %s: 답이 없어 후속 리마인더가 Closed Lost 로 닫았습니다.", conversation_id)


def _advance(conversation_id: int, contact_id: int) -> None:
    """고객이 연락했다 → Negotiating. 수집기(`ticket_history`)가 쓰는 그 함수다."""
    from .ticket_history import _advance_on_customer_reply

    asyncio.run(_advance_on_customer_reply(conversation_id, contact_id))


def _dead_mailboxes(session) -> set[str]:
    """토큰이 죽은 개인 사서함. 그리로 온 답장은 며칠이고 안 들어오는데 시계만 돈다."""
    return {
        f"gmail:{email}"
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
        replies = _replies(session, {contact_id}, after)
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
                | ((Conversation.stage == CLOSED_LOST) & Conversation.followup_closed_at.isnot(None)),
                # SINCE 뒤에 나간 회신이 있는 대화만. `last_outgoing_at` 으로 좁히면 싸지만 그
                # 칸을 안 채우는 발송 경로가 있다(`approval.mark_sent`).
                Conversation.id.in_(
                    select(Message.conversation_id).where(
                        Message.direction == "outgoing", Message.status == "sent",
                        Message.sent_at >= start,
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
        replies = _replies(session, {c.contact_id for c in convs}, start)
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
            base, reminders = sequence_state(outgoing)
            if base is None or _naive(base.sent_at) < start:
                continue
            after = _naive(base.sent_at)
            place = _reply_place(replies, conv.contact_id, conv.id, after)
            if place == "here":
                _advance(conv.id, conv.contact_id)
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
            # 비상 스위치를 내렸는데 시계가 돌면, 한 통도 못 받은 고객이 Lost 가 된다.
            if not delivery_on or not window:
                continue
            if base.channel_account_id in dead:
                continue
            if any(m.status in ("drafting", "pending_approval", "approved", "send_failed")
                   and m.prompt_variant not in REMINDER_VARIANTS for m in outgoing):
                continue  # 사람이 쓰는 중이다 — 몇 분 사이 두 통을 받게 하지 않는다
            acted += 1
            if not _recheck(conv.id, conv.contact_id, after):
                continue
            if step == "close":
                _close(conv.id)
                done["closed"] += 1
            elif _create_reminder(conv.id, REMINDER_1 if step == "send_1" else REMINDER_2):
                done["sent"] += 1
        except Exception:
            logger.warning("후속 리마인더 처리 실패 (conversation=%s)", conv.id, exc_info=True)
    if any(done.values()):
        logger.info("후속 리마인더: 협의 중 %(advanced)d · 발송 %(sent)d · 종료 %(closed)d", done)
    return done
