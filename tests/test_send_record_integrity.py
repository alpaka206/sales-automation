"""발송 쪽에서 메일이 **두 번 기록되거나 빠지는** 자리 (2026-09-29 감사에서 재현하고 고친 것).

발송 경로 자체(배달 뒤 DB 오류 · 개인함 발송 오류 분류 · 노트 재시도 · 단계 미러링)는
`test_send_pipeline_safety.py` 에 있다. 여기는 복구 화면 · 이력 · 요약 쪽이다.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents import followup_sequence as fs
from src.agents import send_worker
from src.db.base import Base
from src.db.models import (
    Contact,
    Conversation,
    ConversationProgress,
    CustomerInteraction,
    Message,
)
from tests.test_followup_sequence import _now, _outgoing, _ticket, db  # noqa: F401 — fixture


def _engine():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


def _approved(factory, **fields) -> int:
    with factory() as session:
        contact = Contact(normalized_email="buyer@example.com", email="buyer@example.com",
                          full_name="Buyer", hubspot_contact_id="C-1")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="new", hubspot_ticket_id="T-1")
        session.add(conv)
        session.flush()
        msg = Message(conversation_id=conv.id, direction="outgoing", status="approved",
                      body="Thanks for reaching out.", subject="RE: Quote",
                      to_address="buyer@example.com", **fields)
        session.add(msg)
        session.commit()
        return msg.id


@pytest.fixture(autouse=True)
def _quiet_worker_state():
    send_worker._sent_timestamps.clear()
    send_worker._daily_count = 0
    yield
    send_worker._sent_timestamps.clear()
    send_worker._daily_count = 0


# ── 1. 배달된 메일이 「발송 실패」가 된다 → 재승인하면 고객이 두 통 받는다 ─────────────────────────

async def test_a_db_error_right_after_delivery_does_not_turn_a_delivered_mail_into_send_failed(monkeypatch):
    """HubSpot 이 200 으로 받았는데 바로 뒤 commit 이 한 번 끊겼다(Supabase 연결 리셋 등).

    지금: `_send_one` 의 `except Exception` 이 그것을 「알 수 없는 발송 실패 = 영구」로 읽어 롤백하고
    `send_failed` 를 찍는다 — `hubspot_message_id` 도 롤백으로 사라진다. `approval.approve` 는
    `send_failed` 를 「고객에게 아무것도 안 갔다」로 보고 다시 승인·발송을 받아 준다 → 같은 메일 두 통.
    """
    state = {"fail": False}

    class FlakySession(Session):
        def commit(self):
            if state["fail"]:
                state["fail"] = False
                raise OperationalError("COMMIT", {}, Exception("server closed the connection"))
            return super().commit()

    factory = sessionmaker(bind=_engine(), class_=FlakySession, expire_on_commit=False)
    monkeypatch.setattr(send_worker, "SessionLocal", factory)
    mid = _approved(factory)
    assert send_worker._claim_id(mid)

    async def delivered(msg):
        msg.hubspot_thread_id = "th-1"
        msg.hubspot_message_id = "hub-1"
        state["fail"] = True  # 배달은 끝났다 — 다음 commit 이 끊긴다

    with patch("src.integrations.senders.send", delivered):
        await send_worker._send_one(mid)

    with factory() as session:
        row = session.get(Message, mid)
        assert row.status != "send_failed", "배달된 메일이 send_failed 로 남아 재승인·재발송이 열린다"
        assert row.hubspot_message_id == "hub-1"


# ── 2. 열쇠 없는 배달 메일은 티켓 이력에 두 번 선다 ─────────────────────────────────────────

def test_a_reply_whose_outcome_was_unknown_is_shown_once_after_its_hubspot_copy_arrives():
    """5xx/타임아웃으로 `delivery_unknown` 이 된 회신 — 실제로는 나갔고 수집기가 허브스팟 사본을 가져왔다.

    `history_view.ticket_records` 는 `messages.hubspot_message_id` 로만 사본을 거르는데 그 칸이 비어
    있다(복구 화면에서 「나간 것 확인」을 눌러도 안 채워진다) → 같은 메일이 이력에 두 줄. 같은 자를
    쓰는 `inbound.thread_events`(초안 문맥) · `mailbox_sync._we_already_sent_it`(perso.ai 사서함 사본)도 같다.
    """
    from src.db.history_view import ticket_records

    factory = sessionmaker(bind=_engine(), expire_on_commit=False)
    with factory() as session:
        contact = Contact(normalized_email="buyer@example.com", email="buyer@example.com", full_name="B")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="meeting_link_sent", hubspot_ticket_id="T-1")
        session.add(conv)
        session.flush()
        at = _now() - timedelta(hours=2)
        session.add(Message(conversation_id=conv.id, direction="outgoing", status="delivery_unknown",
                            body="Thanks for reaching out.", subject="RE: Quote",
                            approved_at=at, created_at=at))
        session.add(CustomerInteraction(
            contact_id=contact.id, conversation_id=conv.id, channel="이메일", direction="outgoing",
            subject="RE: Quote", summary="Thanks for reaching out.",
            external_id="hubspot:conv:hub-77", happened_at=at + timedelta(seconds=20),
        ))
        session.commit()
        records = ticket_records(session, contact.id, [conv.id])[conv.id]
    assert len(records) == 1, [r["record_key"] for r in records]


# ── 3. 「발송 실패 정리」가 갔을지 모르는 리마인더를 지우면 리마인더 1 이 또 나간다 ──────────────

def test_clearing_failures_does_not_let_a_maybe_delivered_reminder_be_sent_again(db):  # noqa: F811
    """리마인더 1 이 5xx 로 `delivery_unknown` → 운영자가 복구 화면의 「발송 실패 정리」를 누름(→ `rejected`)
    → 수집기가 그 리마인더의 허브스팟 사본을 가져옴.

    `outside_replies` 는 사본 알아보기(본문 대조)를 `delivery_unknown`·`sent`·`sending` 리마인더에만 하므로
    `rejected` 가 된 리마인더의 사본은 「우리가 콘솔 밖에서 보낸 새 메일」이 되고 새 기준이 된다. 기준 뒤에
    만든 리마인더가 없으니 시퀀스는 처음부터 — 사흘 뒤 「지난 메일에 이어」가 한 번 더, 이어서 닫기 메일.
    """
    from src.api.main import app

    conv = _ticket(db, sent_days_ago=10)
    fs.run_followup_sequence_once()
    first = _outgoing(db, conv)[1]
    assert first.prompt_variant == fs.REMINDER_1
    with db() as session:
        row = session.get(Message, first.id)
        row.status = "delivery_unknown"
        row.created_at = row.approved_at = _now() - timedelta(days=7)
        session.commit()

    with patch("src.api.routes.recovery.SessionLocal", db), TestClient(app) as client:
        client.post("/operations/recovery/clear-failures", headers={"Origin": "http://testserver"})

    with db() as session:
        row = session.get(Message, first.id)
        # 「갔는지 모른다」는 실패가 아니다 — 정리 버튼이 건드리지 않는다.
        assert row.status == "delivery_unknown"
        session.add(CustomerInteraction(
            contact_id=session.get(Conversation, conv).contact_id, conversation_id=conv,
            channel="이메일", direction="outgoing", summary=row.body,
            external_id="hubspot:conv:r1-copy", happened_at=row.created_at + timedelta(minutes=1),
        ))
        session.commit()
        outside = fs.outside_replies(session, [conv])
    base, reminders = fs.sequence_state(_outgoing(db, conv), outside.get(conv, ()))
    assert fs.next_step(base, reminders)[0] != "send_1", "이미 나간 리마인더 1 이 다시 나간다"


# ── 4. 운영자 「동기화 재시도」가 첫 회차 기록을 다시 쓴다 (보이지는 않음 — 낮음) ─────────────────

async def test_an_operator_sync_retry_does_not_write_the_first_attempt_records_again(monkeypatch):
    """`retry_message_sync` 가 `post_send_sync_attempts` 를 0 으로 되돌리므로 다음 정리가 「첫 회차」가 되어
    `add_progress(..., "reply", "답변 발송 완료: …")` 를 한 번 더 적는다. `reply` 는 읽을 때 걸러져 화면엔
    안 보이지만 append-only 표에 같은 줄이 쌓인다. (요약 한 줄은 `summary_line` 으로 막혀 있다.)"""
    factory = sessionmaker(bind=_engine(), expire_on_commit=False)
    monkeypatch.setattr(send_worker, "SessionLocal", factory)
    monkeypatch.setattr("src.db.conversation_history.SessionLocal", factory)
    monkeypatch.setattr("src.agents.summaries.SessionLocal", factory)
    monkeypatch.setattr("src.integrations.google_sheets.is_configured", lambda: False)
    monkeypatch.setattr("src.integrations.hubspot.move_ticket_stage_after_send", lambda _t: True)
    mid = _approved(factory)
    with factory() as session:
        msg = session.get(Message, mid)
        msg.status = "sent"
        msg.sent_at = _now()
        conv = session.get(Conversation, msg.conversation_id)
        await send_worker._post_send_bookkeeping(session, msg, conv, mid)  # 첫 회차

    from src.api.main import app

    with patch("src.api.routes.recovery.SessionLocal", factory), TestClient(app) as client:
        client.post(f"/operations/recovery/messages/{mid}/sync", headers={"Origin": "http://testserver"})
    with factory() as session:
        msg = session.get(Message, mid)
        conv = session.get(Conversation, msg.conversation_id)
        await send_worker._post_send_bookkeeping(session, msg, conv, mid)
        rows = session.scalars(select(ConversationProgress).where(
            ConversationProgress.conversation_id == conv.id, ConversationProgress.kind == "reply",
        )).all()
    assert len(rows) == 1, [r.detail for r in rows]



# ── 5. 티켓 요약(초안의 AI 문맥)을 다시 만들면 우리 회신이 두 줄, 리마인더는 세 줄 ─────────────────

def test_rebuilding_the_ticket_summary_counts_each_sent_mail_once():
    """`summaries.rebuild_summary` 는 10분 요약 백필이 기록을 채울 때마다 그 티켓 요약을 통째로 다시 만든다.
    재료가 `messages`(나간 회신) + 그 티켓의 `customer_interactions` **전부**인데, 다른 화면들이 쓰는
    열쇠(`messages.hubspot_message_id` ↔ `hubspot:conv:<id>`)로 사본을 안 거른다 → 콘솔 회신 하나가
    자기 줄과 허브스팟 사본의 줄로 두 번 선다(두 줄은 따로 만든 한 줄 요약이라 글자가 달라 `append_line`
    의 같은 불릿 거르기에도 안 걸린다). 리마인더는 발송 뒤 정리가 일부러 요약에 안 보태는데(「답을 세 번
    했다」로 읽힌다) 여기서는 메일 · 사본 · 「Reminder Sent 1」 세 줄이 된다. 이 요약이 초안 프롬프트에 실린다.
    """
    from src.agents.summaries import rebuild_summary

    factory = sessionmaker(bind=_engine(), expire_on_commit=False)
    with factory() as session:
        contact = Contact(normalized_email="buyer@example.com", email="buyer@example.com", full_name="B")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="meeting_link_sent", hubspot_ticket_id="T-1")
        session.add(conv)
        session.flush()
        at = _now() - timedelta(days=4)
        session.add(Message(conversation_id=conv.id, direction="outgoing", status="sent",
                            body="Thanks — here is our plan.", subject="RE: Quote", sent_at=at,
                            created_at=at, hubspot_message_id="hub-1",
                            summary_line="우리: 플랜 안내 회신"))
        session.add(CustomerInteraction(
            contact_id=contact.id, conversation_id=conv.id, channel="이메일", direction="outgoing",
            subject="RE: Quote", summary="Thanks — here is our plan.\n\nBest,\nUntae",
            context="플랜을 안내하는 우리 회신", external_id="hubspot:conv:hub-1",
            happened_at=at + timedelta(seconds=5),
        ))
        session.commit()
        rebuild_summary(session, conv.id)
        lines = (session.get(Conversation, conv.id).summary or "").splitlines()
    assert len(lines) == 1, lines


# ── 6. 개인 사서함 발송의 허브스팟 노트는 한 번 못 남기면 영영 없다 ─────────────────────────────

