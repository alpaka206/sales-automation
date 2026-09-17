"""Contacted 후속 리마인더 — 3일 · 5일 · 7일 (2026-09-17 운영자 지시).

규칙이 틀리면 방금 답한 고객에게 「답이 없으셔서 닫겠습니다」가 나가거나, 답한 적 없는 고객이
기계에 의해 Lost 가 된다. 그래서 「보낸다」보다 **「안 보낸다」** 쪽을 더 많이 고정한다.
설계: docs/후속-회신-시퀀스-설계.md
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents import followup_sequence as fs
from src.common.config import settings
from src.db.base import Base
from src.db.models import Contact, Conversation, CustomerInteraction, Message

TEMPLATES = {
    "followup_reminder": "Hi there,\n\nFollowing up on my last note.\n\nBest,\nUntae Bae",
    "followup_closing": "Hi there,\n\nI will close this inquiry out on our side.\n\nBest,\nUntae Bae",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def db(monkeypatch):
    """StaticPool — 단계 이동이 `asyncio.to_thread` 로 다른 스레드에서 돈다."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    from src.agents import ticket_history
    from src.api.routes import customer_ops

    monkeypatch.setattr(fs, "SessionLocal", factory)
    monkeypatch.setattr(customer_ops, "SessionLocal", factory)
    monkeypatch.setattr(customer_ops, "_sync_stage", AsyncMock(return_value={}))
    monkeypatch.setattr(ticket_history, "sync_one_ticket", AsyncMock(return_value=0))
    monkeypatch.setattr(settings, "FOLLOWUP_SEQUENCE_SINCE", "2026-01-01")
    monkeypatch.setattr(settings, "SEND_WORKER_ENABLED", True)  # 워커가 보낸다 — 여기선 안 보낸다
    monkeypatch.setattr(fs, "in_send_window", lambda now: True)
    monkeypatch.setattr("src.db.email_templates.get_email_template", lambda key, language=None: TEMPLATES.get(key))
    return factory


def _ticket(factory, *, sent_days_ago: float, stage: str = "meeting_link_sent",
            language: str = "en", **base_fields) -> int:
    with factory() as session:
        contact = Contact(normalized_email="buyer@example.com", email="buyer@example.com",
                          full_name="Buyer")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage=stage, hubspot_ticket_id="T-1",
                            inquiry_subject="Custom quote", inquiry_language=language)
        session.add(conv)
        session.flush()
        session.add(Message(conversation_id=conv.id, direction="inbound", body="quote please",
                            created_at=_now() - timedelta(days=sent_days_ago + 1)))
        session.add(Message(
            conversation_id=conv.id, direction="outgoing", status="sent", body="our answer",
            to_address="buyer@example.com", subject="RE: Custom quote", language=language,
            target_language=language, sent_at=_now() - timedelta(days=sent_days_ago),
            **base_fields,
        ))
        session.commit()
        return conv.id


def _outgoing(factory, conv_id):
    with factory() as session:
        return session.query(Message).filter_by(conversation_id=conv_id, direction="outgoing") \
            .order_by(Message.id).all()


def _stage(factory, conv_id):
    with factory() as session:
        return session.get(Conversation, conv_id).stage


def _mark_sent(factory, message_id, days_ago):
    with factory() as session:
        row = session.get(Message, message_id)
        row.status = "sent"
        row.sent_at = _now() - timedelta(days=days_ago)
        session.commit()


# ---- 안 보낸다 -----------------------------------------------------------------

def test_nothing_happens_while_the_switch_date_is_empty(db, monkeypatch):
    monkeypatch.setattr(settings, "FOLLOWUP_SEQUENCE_SINCE", "")
    conv = _ticket(db, sent_days_ago=10)
    assert fs.run_followup_sequence_once() == {}
    assert len(_outgoing(db, conv)) == 1


def test_a_reply_sent_before_the_switch_date_never_starts_the_clock(db, monkeypatch):
    """기존 티켓은 안 건드린다 — 운영자 지시가 이 날짜 한 칸이다."""
    monkeypatch.setattr(settings, "FOLLOWUP_SEQUENCE_SINCE", (_now() - timedelta(days=5)).strftime("%Y-%m-%d"))
    conv = _ticket(db, sent_days_ago=10)
    fs.run_followup_sequence_once()
    assert len(_outgoing(db, conv)) == 1


def test_nothing_goes_out_before_three_days(db):
    conv = _ticket(db, sent_days_ago=2.9)
    fs.run_followup_sequence_once()
    assert len(_outgoing(db, conv)) == 1


def test_a_customer_reply_moves_the_ticket_to_negotiating_and_sends_nothing(db):
    conv = _ticket(db, sent_days_ago=4)
    with db() as session:
        contact_id = session.get(Conversation, conv).contact_id
        session.add(CustomerInteraction(contact_id=contact_id, conversation_id=conv, channel="email",
                                        direction="inbound", summary="sounds good",
                                        external_id="hubspot:conv:9", happened_at=_now() - timedelta(days=1)))
        session.commit()
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "negotiation"
    assert len(_outgoing(db, conv)) == 1


def test_a_reply_in_another_ticket_of_the_same_person_stops_the_reminder_without_moving(db):
    """개인함 메일은 그 고객의 **최신** 대화에 붙는다 — 대화 단위로 보면 방금 답한 고객을 재촉한다."""
    conv = _ticket(db, sent_days_ago=4)
    with db() as session:
        contact_id = session.get(Conversation, conv).contact_id
        other = Conversation(contact_id=contact_id, stage="new", hubspot_ticket_id="T-2")
        session.add(other)
        session.flush()
        session.add(CustomerInteraction(contact_id=contact_id, conversation_id=other.id, channel="email",
                                        direction="inbound", summary="new question",
                                        external_id="gmail:abc", happened_at=_now() - timedelta(hours=5)))
        session.commit()
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "meeting_link_sent"
    assert len(_outgoing(db, conv)) == 1


def test_the_emergency_mail_switch_stops_the_clock(db, monkeypatch):
    """메일만 막았는데 시계가 돌면, 한 통도 못 받은 고객이 기계에 의해 Lost 가 된다."""
    from src.common import safe_mode

    monkeypatch.setattr(safe_mode, "EMAIL_SENDING_ENABLED", False)
    conv = _ticket(db, sent_days_ago=20)
    fs.run_followup_sequence_once()
    assert len(_outgoing(db, conv)) == 1
    assert _stage(db, conv) == "meeting_link_sent"


def test_outside_office_hours_it_waits(db, monkeypatch):
    monkeypatch.setattr(fs, "in_send_window", lambda now: False)
    conv = _ticket(db, sent_days_ago=4)
    fs.run_followup_sequence_once()
    assert len(_outgoing(db, conv)) == 1


def test_the_window_is_weekday_office_hours_in_korea():
    assert fs.in_send_window(datetime(2026, 9, 17, 1, 0))       # 목 10:00 KST
    assert not fs.in_send_window(datetime(2026, 9, 17, 12, 0))  # 목 21:00 KST
    assert not fs.in_send_window(datetime(2026, 9, 19, 1, 0))   # 토 10:00 KST


def test_a_missing_template_sends_nothing(db, monkeypatch):
    """본문은 코드가 지어내지 않는다 — 콘솔에 없으면 안 보낸다."""
    monkeypatch.setattr("src.db.email_templates.get_email_template", lambda key, language=None: None)
    conv = _ticket(db, sent_days_ago=4)
    fs.run_followup_sequence_once()
    assert len(_outgoing(db, conv)) == 1


def test_a_draft_the_operator_is_writing_holds_the_reminder(db):
    conv = _ticket(db, sent_days_ago=4)
    with db() as session:
        session.add(Message(conversation_id=conv, direction="outgoing", status="pending_approval",
                            body="writing", prompt_variant="manual"))
        session.commit()
    fs.run_followup_sequence_once()
    assert [m.prompt_variant for m in _outgoing(db, conv)] == [None, "manual"]


def test_if_the_last_hubspot_check_fails_nothing_goes_out(db, monkeypatch):
    from src.agents import ticket_history

    monkeypatch.setattr(ticket_history, "sync_one_ticket", AsyncMock(side_effect=RuntimeError("429")))
    conv = _ticket(db, sent_days_ago=4)
    fs.run_followup_sequence_once()
    assert len(_outgoing(db, conv)) == 1


# ---- 보낸다 --------------------------------------------------------------------

def test_after_three_days_one_reminder_copies_the_reply_it_follows(db):
    conv = _ticket(db, sent_days_ago=3.1, signature_key="signature_untae",
                   cc_addresses="boss@example.com", channel_account_id="gmail:untae@estsoft.com")
    fs.run_followup_sequence_once()
    fs.run_followup_sequence_once()  # 두 번 돌려도 하나
    rows = _outgoing(db, conv)
    assert len(rows) == 2
    reminder = rows[1]
    assert reminder.prompt_variant == fs.REMINDER_1
    assert reminder.status == "approved" and reminder.approved_by == fs.APPROVER
    assert reminder.body == TEMPLATES["followup_reminder"]
    assert (reminder.to_address, reminder.subject) == ("buyer@example.com", "RE: Custom quote")
    # 같은 사람이 같은 문으로 이어 쓴다 — 개인 사서함으로 나간 회신이면 리마인더도 그 사서함에서.
    assert reminder.signature_key == "signature_untae"
    assert reminder.cc_addresses == "boss@example.com"
    assert reminder.channel_account_id == "gmail:untae@estsoft.com"
    assert (reminder.language, reminder.target_language) == ("en", "en")


def test_a_korean_customer_gets_the_english_template_translated(db, monkeypatch):
    monkeypatch.setattr("src.llm.translate.translate_to",
                        lambda text, target, llm=None: "안녕하세요, 지난 메일에 이어 연락드립니다.")
    conv = _ticket(db, sent_days_ago=4, language="ko")
    fs.run_followup_sequence_once()
    reminder = _outgoing(db, conv)[1]
    assert reminder.body.startswith("안녕하세요")
    assert (reminder.language, reminder.target_language) == ("ko", "ko")


def test_a_failed_translation_sends_nothing(db, monkeypatch):
    """번역기가 ""를 돌려주면 영문 원문이 한국어 고객에게 가는 대신 이번 회차를 건너뛴다."""
    monkeypatch.setattr("src.llm.translate.translate_to", lambda text, target, llm=None: "")
    conv = _ticket(db, sent_days_ago=4, language="ja")
    fs.run_followup_sequence_once()
    assert len(_outgoing(db, conv)) == 1


def test_three_five_seven_and_then_closed_lost(db):
    conv = _ticket(db, sent_days_ago=30)
    fs.run_followup_sequence_once()
    first = _outgoing(db, conv)[1]
    _mark_sent(db, first.id, days_ago=4.9)
    fs.run_followup_sequence_once()
    assert len(_outgoing(db, conv)) == 2  # 5일이 안 됐다

    _mark_sent(db, first.id, days_ago=5.1)
    fs.run_followup_sequence_once()
    second = _outgoing(db, conv)[2]
    assert second.prompt_variant == fs.REMINDER_2
    assert second.body == TEMPLATES["followup_closing"]

    _mark_sent(db, second.id, days_ago=6.9)
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "meeting_link_sent"

    _mark_sent(db, second.id, days_ago=7.1)
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "closed_lost"
    with db() as session:
        assert session.get(Conversation, conv).followup_closed_at is not None


def test_a_reply_after_the_automatic_close_brings_the_ticket_back_red(db):
    """7일이 지나 닫았어도 답장이 오면 협의 중으로 — 그 티켓은 빨갛게 선다(운영자)."""
    conv = _ticket(db, sent_days_ago=30)
    with db() as session:
        c = session.get(Conversation, conv)
        c.stage, c.followup_closed_at = "closed_lost", _now() - timedelta(days=1)
        session.add(CustomerInteraction(contact_id=c.contact_id, conversation_id=conv, channel="email",
                                        direction="inbound", summary="sorry, back now",
                                        external_id="hubspot:conv:77", happened_at=_now()))
        session.commit()
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "negotiation"
    with db() as session:
        view = fs.view(session.get(Conversation, conv), [])
    assert view["state"] == "revived"


def test_a_ticket_a_person_closed_is_not_revived(db):
    conv = _ticket(db, sent_days_ago=30, stage="closed_lost")
    with db() as session:
        c = session.get(Conversation, conv)
        session.add(CustomerInteraction(contact_id=c.contact_id, conversation_id=conv, channel="email",
                                        direction="inbound", summary="hi", external_id="hubspot:conv:78",
                                        happened_at=_now()))
        session.commit()
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "closed_lost"


# ---- 상태는 행에서 읽는다 ------------------------------------------------------

def _msg(id_, *, variant=None, status="sent", days_ago=0.0):
    return Message(id=id_, direction="outgoing", status=status, prompt_variant=variant, body="x",
                   sent_at=(_now() - timedelta(days=days_ago)) if status == "sent" else None)


def test_a_new_human_reply_restarts_the_clock_from_reminder_one():
    """R1 뒤에 사람이 또 보내면 다음은 R2 가 아니라 R1 이다 — 사람 메일 사흘 뒤 「닫겠습니다」가 안 나간다."""
    base, reminders = fs.sequence_state([
        _msg(1, days_ago=10), _msg(2, variant=fs.REMINDER_1, days_ago=6), _msg(3, days_ago=1),
    ])
    assert base.id == 3 and reminders == {}
    assert fs.next_step(base, reminders)[0] == "send_1"


def test_a_failed_reminder_stops_the_clock_instead_of_repeating():
    base, reminders = fs.sequence_state([
        _msg(1, days_ago=10), _msg(2, variant=fs.REMINDER_1, status="send_failed"),
    ])
    assert fs.next_step(base, reminders) == ("stalled", None)
    waiting = fs.sequence_state([_msg(1, days_ago=10), _msg(2, variant=fs.REMINDER_1, status="approved")])
    assert fs.next_step(*waiting) == ("sending", None)


def test_the_stage_sweep_keeps_a_failed_reminder_while_the_ticket_is_contacted(db):
    """지우면 시퀀스가 「아직 안 보냈다」로 읽고 다시 만든다 — 실패가 10분마다 되풀이된다."""
    from src.agents.stage_sync import _retire_superseded_drafts

    conv = _ticket(db, sent_days_ago=10)
    with db() as session:
        session.add(Message(conversation_id=conv, direction="outgoing", status="send_failed",
                            body="r1", prompt_variant=fs.REMINDER_1))
        session.commit()
        _retire_superseded_drafts(session, conv, "meeting_link_sent")
        session.commit()
    assert [m.prompt_variant for m in _outgoing(db, conv)] == [None, fs.REMINDER_1]
    with db() as session:
        _retire_superseded_drafts(session, conv, "negotiation")
        session.commit()
    assert [m.prompt_variant for m in _outgoing(db, conv)] == [None]


def test_a_customer_reply_clears_a_reminder_the_worker_has_not_picked_up(db):
    from src.api.routes.customer_ops import _set_conversation_stage

    conv = _ticket(db, sent_days_ago=10)
    with db() as session:
        session.add(Message(conversation_id=conv, direction="outgoing", status="approved",
                            body="r1", prompt_variant=fs.REMINDER_1))
        session.add(Message(conversation_id=conv, direction="outgoing", status="pending_approval",
                            body="mine", prompt_variant="manual"))
        session.commit()
    _set_conversation_stage(conv, "negotiation", retire_drafts=False)
    assert [m.prompt_variant for m in _outgoing(db, conv)] == [None, "manual"]


def test_the_send_path_finds_the_two_templates_by_name():
    from src.db.email_templates import is_code_resolved

    assert all(is_code_resolved(key) for key in fs.TEMPLATE_KEYS.values())


def test_a_reminder_is_never_redrafted_by_the_model(db, monkeypatch):
    from src.agents import inbound_worker

    monkeypatch.setattr(inbound_worker, "SessionLocal", db)
    conv = _ticket(db, sent_days_ago=10)
    with db() as session:
        row = Message(conversation_id=conv, direction="outgoing", status="send_failed",
                      body="r1", prompt_variant=fs.REMINDER_1)
        session.add(row)
        session.commit()
        message_id = row.id
    with pytest.raises(inbound_worker.RedraftError):
        inbound_worker.request_redraft(message_id)


def test_the_start_date_is_midnight_in_korea(monkeypatch):
    """열이 UTC 라 그대로 두면 그날 오전 9시 전에 나간 회신이 「기존 티켓」으로 빠진다."""
    monkeypatch.setattr(settings, "FOLLOWUP_SEQUENCE_SINCE", "2026-09-18")
    assert fs.since() == datetime(2026, 9, 17, 15, 0)


def test_an_old_ticket_starts_only_from_a_mail_sent_after_the_start_date(db):
    """「리마인더는 새 메일부터」(2026-09-17 운영자). 기존 티켓은 새 메일이 없으면 영영 조용하고,
    그 날짜 뒤에 콘솔에서 새로 보낸 메일이 있으면 **그 메일부터** 3일을 센다."""
    quiet = _ticket(db, sent_days_ago=400)
    fs.run_followup_sequence_once()
    assert len(_outgoing(db, quiet)) == 1

    with db() as session:
        session.add(Message(conversation_id=quiet, direction="outgoing", status="sent", body="new follow-up",
                            to_address="buyer@example.com", subject="RE: Custom quote", language="en",
                            target_language="en", prompt_variant="manual", sent_at=_now() - timedelta(days=1)))
        session.commit()
    fs.run_followup_sequence_once()
    assert len(_outgoing(db, quiet)) == 2  # 새 메일 뒤 1일 — 아직

    _mark_sent(db, _outgoing(db, quiet)[1].id, days_ago=3.1)
    fs.run_followup_sequence_once()
    assert _outgoing(db, quiet)[-1].prompt_variant == fs.REMINDER_1


def test_a_misspelled_template_key_shows_on_the_ticket(db, monkeypatch):
    """키를 틀리게 적으면 스윕은 로그만 남기고 안 보낸다 — 티켓 화면이 그걸 말해야 누가 안다."""
    monkeypatch.setattr("src.db.email_templates.get_email_template", lambda key, language=None: None)
    conv = _ticket(db, sent_days_ago=4)
    with db() as session:
        view = fs.view(session.get(Conversation, conv), _outgoing(db, conv))
    assert view["state"] == "send_1" and view["template_missing"] == "followup_reminder"
