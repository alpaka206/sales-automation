"""콘솔 밖에서 보낸 첫 답장 — 단계와 「문의 회신」 라벨, 그리고 리마인더 한 줄 (2026-10-08 운영자 지시).

- 「다른곳에서 보냈어도 첫번째 답변이면 문의 회신으로 떠야해」 — 허브스팟 받은편지함 · 개인 메일함에서 나간 첫
  답이 「문의 회신」이고, 그 뒤의 후속 리마인더는 아니다.
- 「문의 접수 후에 그 사이트에서 안보냈더니 … 이메일 수신이 왔음에도 negotation 으로 안옮겨졌어」 — 그런 New 티켓은
  Contacted, 그 뒤 고객이 썼으면 협의 중.
- 「이렇게 한번씩 더 나감 위에 요약본은 필요없어」 — 리마인더 하나는 한 줄.

자는 하나다: `inbound.first_sales_reply`(이 문의 뒤 우리 영업의 첫 이메일). 단계는 `ticket_history.
advance_if_customer_replied`, 라벨은 `routes.messages._message_detail_context` 의 `first_reply_key`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents import inbound, ticket_history
from src.db.base import Base
from src.db.models import Contact, Conversation, CustomerInteraction, Message

T0 = datetime(2026, 10, 8, 2, 18)  # 문의가 들어온 때(UTC)
_MIN = timedelta(minutes=1)


@pytest.fixture()
def db(monkeypatch):
    """New 티켓 하나 — 접수가 만든 문의 행과, 그 문의에 쓴 자동 초안(아직 검토 중)."""
    from src.api.routes import customer_ops
    from src.api.routes import messages as messages_route

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    for module in (ticket_history, inbound, customer_ops, messages_route):
        monkeypatch.setattr(module, "SessionLocal", factory)
    with factory() as session:
        contact = Contact(normalized_email="op@school.example", email="op@school.example", full_name="Op")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="new", hubspot_ticket_id="T-9", created_at=T0)
        session.add(conv)
        session.flush()
        session.add(Message(conversation_id=conv.id, direction="inbound", body="22명분 스타터 일괄 결제가 되나요?",
                            status="received", created_at=T0))
        session.add(Message(conversation_id=conv.id, direction="outgoing", body="자동 초안", subject="RE: 문의",
                            status="pending_approval", created_at=T0 + 5 * _MIN))
        session.commit()
        return factory, conv.id, contact.id


def _line(factory, conv_id, contact_id, at, direction, ext, *, handler=None, summary="메일") -> int:
    with factory() as session:
        row = CustomerInteraction(contact_id=contact_id, conversation_id=conv_id, channel="이메일",
                                  direction=direction, handler=handler, summary=summary, external_id=ext,
                                  happened_at=at)
        session.add(row)
        session.commit()
        return row.id


def _watch_moves(monkeypatch, sheets: list | None = None) -> list[tuple[int, str]]:
    moved: list[tuple[int, str]] = []

    async def _move(conversation_id, contact_id, stage, sheet_client_id):
        moved.append((conversation_id, stage))
        if sheets is not None:
            sheets.append(sheet_client_id)

    monkeypatch.setattr(ticket_history, "_move_answered_elsewhere", _move)
    return moved


def _judge(conv_id) -> bool:
    return asyncio.run(ticket_history.advance_if_customer_replied(conv_id))


# ── 단계 ─────────────────────────────────────────────────────────────────────────────────────────


def test_an_answer_sent_from_the_hubspot_inbox_moves_a_new_ticket_to_contacted(db, monkeypatch):
    factory, conv_id, contact_id = db
    moved = _watch_moves(monkeypatch)
    _line(factory, conv_id, contact_id, T0 + 88 * _MIN, "outgoing", "hubspot:conv:ours",
          handler="Untae <perso.ai@estsoft.com>")
    assert _judge(conv_id) and moved == [(conv_id, "meeting_link_sent")]


def test_a_customer_reply_after_that_answer_moves_it_to_negotiating_even_if_we_wrote_again(db, monkeypatch):
    """오늘 사례 그대로: 13:46 우리(허브스팟) → 14:04 고객(개인 메일함) → 14:33 우리. 고객의 말은 우리 **첫** 답
    뒤로 잰다 — 마지막 답 뒤로 재면 우리가 또 쓴 것이 고객의 답장을 가린다."""
    factory, conv_id, contact_id = db
    moved = _watch_moves(monkeypatch)
    _line(factory, conv_id, contact_id, T0 + 148 * _MIN, "outgoing", "hubspot:conv:ours-1",
          handler="perso.ai@estsoft.com")
    _line(factory, conv_id, contact_id, T0 + 166 * _MIN, "inbound", "gmail:theirs")
    _line(factory, conv_id, contact_id, T0 + 195 * _MIN, "outgoing", "hubspot:conv:ours-2",
          handler="perso.ai@estsoft.com")
    assert _judge(conv_id) and moved == [(conv_id, "negotiation")]


def test_a_new_ticket_nobody_answered_stays_new(db, monkeypatch):
    """우리 영업 메일이 없으면 고객이 몇 번을 써도 New 다 — 검토할 초안이 대기 중이다."""
    factory, conv_id, contact_id = db
    moved = _watch_moves(monkeypatch)
    _line(factory, conv_id, contact_id, T0 + 30 * _MIN, "inbound", "hubspot:conv:again")
    assert not _judge(conv_id) and moved == []


def test_older_mail_and_cs_answers_are_not_our_first_reply(db, monkeypatch):
    """이 문의 전부터 돌던 메일(개인 메일함이 최신 티켓에 붙인 지난 메일)과 CS 주소의 안내는 우리 첫 답이 아니다."""
    factory, conv_id, contact_id = db
    moved = _watch_moves(monkeypatch)
    _line(factory, conv_id, contact_id, T0 - timedelta(days=2), "outgoing", "gmail:old", handler="rep@estsoft.com")
    _line(factory, conv_id, contact_id, T0 + 60 * _MIN, "outgoing", "hubspot:conv:cs",
          handler="Perso Support <support@perso.ai>")
    assert not _judge(conv_id) and moved == []


def test_the_move_retires_the_new_draft_and_moves_both_stage_columns(db, monkeypatch):
    """옮기는 것은 보드가 카드를 옮길 때와 같은 함수다 — 자동 초안은 답이 이미 다른 길로 나갔으니 지운다."""
    from src.api.routes import customer_ops
    from src.db.models import CustomerProfile

    factory, conv_id, contact_id = db
    synced: list[tuple[str, int | None]] = []

    async def _sync(ticket_id, stage, contact_id, sheet_client_id=None):
        synced.append((stage, sheet_client_id))
        return {}

    monkeypatch.setattr(customer_ops, "_sync_stage", _sync)
    with factory() as session:
        # 이 사람의 옛 문의 행이 워크북에 있다 — 이 문의의 행은 아직 없다.
        session.get(Contact, contact_id).sheet_client_id = 1001
        session.commit()
    _line(factory, conv_id, contact_id, T0 + 88 * _MIN, "outgoing", "hubspot:conv:ours",
          handler="perso.ai@estsoft.com")
    assert _judge(conv_id)
    with factory() as session:
        assert session.get(Conversation, conv_id).stage == "meeting_link_sent"
        assert session.get(CustomerProfile, contact_id).pipeline_stage == "meeting_link_sent"
        assert not session.query(Message).filter_by(conversation_id=conv_id, status="pending_approval").count()
    # 워크북은 이 문의의 행에만 — 연락처의 번호로 찾으면 행이 하나뿐인 옛 문의 행에 Contacted 가 적힌다.
    assert synced == [("meeting_link_sent", None)]


def test_the_poller_rejudges_only_new_tickets_with_outgoing_mail(db, monkeypatch):
    """배포 전에 들어온 줄(오늘 사례)은 다음 줄이 올 때까지 아무도 다시 안 본다 — 폴러가 본다. 후보는 New 이면서
    나간 이메일 줄이 있는 대화뿐이다."""
    factory, conv_id, contact_id = db
    moved = _watch_moves(monkeypatch)
    _line(factory, conv_id, contact_id, T0 + 88 * _MIN, "outgoing", "hubspot:conv:ours",
          handler="perso.ai@estsoft.com")
    with factory() as session:
        quiet = Conversation(contact_id=contact_id, stage="new", created_at=T0)
        session.add(quiet)
        session.commit()
        quiet_id = quiet.id
    _line(factory, quiet_id, contact_id, T0 + 10 * _MIN, "inbound", "hubspot:conv:only-them")
    asked: list[int] = []
    real = ticket_history.advance_if_customer_replied

    async def _spy(conversation_id):
        asked.append(conversation_id)
        return await real(conversation_id)

    monkeypatch.setattr(ticket_history, "advance_if_customer_replied", _spy)
    assert ticket_history.advance_answered_elsewhere_once() == 1
    assert asked == [conv_id] and moved == [(conv_id, "meeting_link_sent")]


def test_first_sales_reply_skips_reminders_bots_cs_and_mail_from_before_the_inquiry():
    from src.agents.inbound import _Turn, first_sales_reply

    def turn(minutes, role, ref):
        return _Turn(at=T0 + minutes * _MIN, direction="outgoing", subject=None, body="x", source_ref=ref, role=role)

    events = [
        turn(-24 * 60, "sales", "interaction:1"),   # 지난 대화
        turn(10, "bot", "interaction:2"),
        turn(20, "cs", "interaction:3"),
        turn(30, "reminder", "message:4"),
        turn(40, "sales", "interaction:5"),
        turn(50, "sales", "message:6"),
    ]
    assert first_sales_reply(events, T0).source_ref == "interaction:5"
    assert first_sales_reply(events[:4], T0) is None


# ── 티켓 화면 ─────────────────────────────────────────────────────────────────────────────────────


def _contacted_with_outside_reply_and_a_reminder(factory, conv_id, contact_id):
    """허브스팟에서 첫 답 → 콘솔의 후속 리마인더 1 (+ 2026-09-21 ~ 10-08 에 따로 남던 「Reminder Sent 1」 줄)."""
    from src.agents.followup_sequence import REMINDER_NOTE_PREFIX

    first_reply = _line(factory, conv_id, contact_id, T0 + 35 * _MIN, "outgoing", "hubspot:conv:first",
                        handler="perso.ai@estsoft.com", summary="Olá Filipe, obrigado pelo contato.")
    with factory() as session:
        conv = session.get(Conversation, conv_id)
        conv.stage = "meeting_link_sent"
        session.query(Message).filter_by(conversation_id=conv_id, status="pending_approval").delete()
        at = T0 + timedelta(days=5)
        reminder = Message(conversation_id=conv_id, direction="outgoing", status="sent", sent_at=at, created_at=at,
                           body="Olá, acompanhando minha última mensagem.", subject="RE: cursos",
                           prompt_variant="followup_reminder_1")
        session.add(reminder)
        session.flush()
        session.add(CustomerInteraction(contact_id=contact_id, conversation_id=conv_id, channel="이메일",
                                        direction="outgoing", summary="Reminder Sent 1",
                                        external_id=f"{REMINDER_NOTE_PREFIX}{reminder.id}", happened_at=at))
        session.commit()
        return first_reply, reminder.id


def test_the_outside_first_reply_is_the_inquiry_reply_and_the_reminder_is_one_line(db):
    from src.api.routes.messages import _message_detail_context

    factory, conv_id, contact_id = db
    first_reply, reminder_id = _contacted_with_outside_reply_and_a_reminder(factory, conv_id, contact_id)
    ctx = _message_detail_context(conversation_id=conv_id)

    assert ctx["first_reply_key"] == f"interaction:{first_reply}"
    bubble = next(b for b in ctx["thread"] if b["id"] == reminder_id)
    assert bubble["reminder_label"] == "Reminder Sent 1"
    assert "Reminder Sent 1" not in [item["summary"] for item in ctx["ticket_interactions"]]


def test_a_console_first_reply_is_still_the_inquiry_reply(db):
    from src.api.routes.messages import _message_detail_context

    factory, conv_id, contact_id = db
    with factory() as session:
        reply = Message(conversation_id=conv_id, direction="outgoing", status="sent", body="답변드립니다.",
                        subject="RE: 문의", sent_at=T0 + 30 * _MIN, created_at=T0 + 25 * _MIN)
        session.add(reply)
        session.commit()
        reply_id = reply.id
    _line(factory, conv_id, contact_id, T0 + 90 * _MIN, "outgoing", "hubspot:conv:later",
          handler="perso.ai@estsoft.com")
    assert _message_detail_context(conversation_id=conv_id)["first_reply_key"] == f"message:{reply_id}"


def test_an_inquiry_logged_after_our_outside_answer_is_not_the_customers_reply(db, monkeypatch):
    """서버가 자는 동안 허브스팟에서 먼저 답했고 접수 행은 그 뒤에 섰다 — 그 행의 시각은 고객이 쓴 때가 아니다
    (`inbound.first_inquiry_ref`). 고객 답장으로 세면 고객이 한 마디도 안 했는데 협의 중으로 간다."""
    factory, conv_id, contact_id = db
    moved = _watch_moves(monkeypatch)
    with factory() as session:
        inquiry = session.query(Message).filter_by(conversation_id=conv_id, direction="inbound").one()
        inquiry.created_at = T0 + 90 * _MIN
        session.commit()
    _line(factory, conv_id, contact_id, T0 + 20 * _MIN, "outgoing", "hubspot:conv:ours",
          handler="perso.ai@estsoft.com")
    assert _judge(conv_id) and moved == [(conv_id, "meeting_link_sent")]


# ── 한 번만 · 이 문의의 것만 ─────────────────────────────────────────────────────────────────────────


def test_a_ticket_put_back_to_new_is_not_moved_again(db, monkeypatch):
    """옮긴 뒤 사람이 New 로 되돌리면 그대로 둔다 — 폴러가 10분마다 다시 옮기면 사람과 싸우고, 그때마다 허브스팟과
    워크북까지 다시 쓴다. 누가 옮겼든 New 를 한 번 떠난 문의(`left_new_at`)는 자동 이동의 대상이 아니다."""
    from src.api.routes import customer_ops

    factory, conv_id, contact_id = db

    async def _sync(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(customer_ops, "_sync_stage", _sync)
    _line(factory, conv_id, contact_id, T0 + 88 * _MIN, "outgoing", "hubspot:conv:ours",
          handler="perso.ai@estsoft.com")
    assert _judge(conv_id)
    with factory() as session:
        conv = session.get(Conversation, conv_id)
        assert conv.left_new_at is not None
        conv.stage = "new"  # 보드에서 사람이 되돌렸다
        session.commit()
    _line(factory, conv_id, contact_id, T0 + 300 * _MIN, "inbound", "hubspot:conv:later")

    asked: list[int] = []
    real = ticket_history.advance_if_customer_replied

    async def _spy(conversation_id):
        asked.append(conversation_id)
        return await real(conversation_id)

    assert not _judge(conv_id)
    monkeypatch.setattr(ticket_history, "advance_if_customer_replied", _spy)
    assert ticket_history.advance_answered_elsewhere_once() == 0 and asked == []
    with factory() as session:
        assert session.get(Conversation, conv_id).stage == "new"


def test_the_stage_hook_keeps_the_first_time_a_ticket_left_new():
    """`left_new_at` 은 단계를 쓰는 모든 길(보드 · 허브스팟 동기화 · 발송 워커 · 백필 · 이 자동 이동)이 ORM 대입이라
    `models._stage_left_new` 한 곳이 적는다. 처음부터 다른 단계로 만든 문의도 — New 였던 적이 없다."""
    fresh = Conversation(contact_id=1, stage="new")
    fresh.stage = "initial"
    assert fresh.left_new_at is None
    fresh.stage = "meeting_link_sent"
    assert fresh.left_new_at is not None
    # 고정한 옛 시각 — `now()` 둘은 윈도우 시계에서 같은 값이 나와 「덮어썼다」와 「남겼다」를 못 가른다.
    fresh.left_new_at = first = datetime(2026, 10, 1, 9, 0)
    fresh.stage = "new"
    fresh.stage = "negotiation"
    assert fresh.left_new_at == first
    assert Conversation(contact_id=1, stage="won").left_new_at is not None


def test_personal_mailbox_mail_waits_while_the_contact_has_another_open_inquiry(db, monkeypatch):
    """개인 메일함 메일은 주제와 무관하게 가장 최근 문의에 붙는다(`mailbox_sync._newest_conversation`). 이 사람과 진행
    중인 다른 문의(수주 고객의 계정 관리 메일 같은)가 있으면 그 메일이 이 문의의 답인지 모른다 — 옮기면 이 문의의
    초안을 지운다. 다른 문의가 끝났으면(Concluded · Closed Lost) 이 문의의 답이다."""
    factory, conv_id, contact_id = db
    moved = _watch_moves(monkeypatch)
    with factory() as session:
        account = Conversation(contact_id=contact_id, stage="won", created_at=T0 - timedelta(days=90))
        session.add(account)
        session.commit()
        account_id = account.id
    _line(factory, conv_id, contact_id, T0 + 60 * _MIN, "outgoing", "gmail:invoice", handler="rep@estsoft.com")
    assert not _judge(conv_id) and moved == []

    with factory() as session:
        session.get(Conversation, account_id).stage = "closed"
        session.commit()
    assert _judge(conv_id) and moved == [(conv_id, "meeting_link_sent")]


def test_the_sheet_id_handed_to_the_move_is_the_inquirys_own(db, monkeypatch):
    factory, conv_id, contact_id = db
    sheets: list = []
    _watch_moves(monkeypatch, sheets)
    with factory() as session:
        session.get(Contact, contact_id).sheet_client_id = 1001
        session.get(Conversation, conv_id).sheet_client_id = 2002
        session.commit()
    _line(factory, conv_id, contact_id, T0 + 88 * _MIN, "outgoing", "hubspot:conv:ours",
          handler="perso.ai@estsoft.com")
    assert _judge(conv_id) and sheets == [2002]


def test_only_a_reminder_that_went_out_says_reminder_sent(db):
    """화면이 그리는 리마인더 중 갔는지 모르는 것(`delivery_unknown`)과 SAFE 모드로 남은 것(`test_sent` — 고객이 받은
    것이 없다)에 「Reminder Sent 1」을 달면 화면이 갔다고 말한다. 칩(`reminder_status`)과 같은 자(`sent` + `sent_at`)."""
    from src.api.routes.messages import _message_detail_context
    from src.db.history_view import ticket_records

    factory, conv_id, contact_id = db
    _first, reminder_id = _contacted_with_outside_reply_and_a_reminder(factory, conv_id, contact_id)
    for status, sent_at in (("delivery_unknown", None), ("test_sent", T0 + timedelta(days=5))):
        with factory() as session:
            reminder = session.get(Message, reminder_id)
            reminder.status, reminder.sent_at = status, sent_at
            session.commit()
            records = ticket_records(session, contact_id, [conv_id])[conv_id]

        bubble = next(b for b in _message_detail_context(conversation_id=conv_id)["thread"] if b["id"] == reminder_id)
        assert bubble["reminder_label"] is None, status
        assert next(r for r in records if r["record_key"] == f"message:{reminder_id}")["tag"] is None, status
