"""Contacted 후속 리마인더 — 3일 · 5일 · 7일 (2026-09-17 운영자 지시).

규칙이 틀리면 방금 답한 고객에게 「답이 없으셔서 닫겠습니다」가 나가거나, 답한 적 없는 고객의
문의가 기계에 의해 종결된다(닫는 단계는 2026-09-30 부터 Concluded). 그래서 「보낸다」보다 **「안 보낸다」** 쪽을 더 많이 고정한다.
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


def test_a_reminder_after_a_new_thread_first_reply_keeps_that_thread_with_one_re(db):
    """첫 회신이 새 제목(RE: 없이 — 폼 · 채팅 문의는 이어 붙을 메일 스레드가 없다)으로 나갔으면 리마인더는 그
    제목에 RE: 하나를 붙여 같은 스레드로 간다. 허브스팟 티켓 이름(「Custom quote」)으로 떨어지지 않는다."""
    conv = _ticket(db, sent_days_ago=3.1)
    with db() as session:
        session.query(Message).filter_by(conversation_id=conv, direction="outgoing").one().subject = \
            "Dubbing your 40 training videos"
        session.commit()
    fs.run_followup_sequence_once()
    reminder = _outgoing(db, conv)[1]
    assert reminder.subject == "RE: Dubbing your 40 training videos"


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


def test_a_reminder_written_in_the_customers_language_goes_out_as_written(db, monkeypatch):
    """그 언어로 쓴 행(`followup_reminder_ko`)이 있으면 번역하지 않고 쓴 그대로 나간다 — 2026-10-07 운영자:
    「리마인더는 써있는거 그대로 보내져야한다」. 없는 언어만 영문 행을 번역한다(위 두 테스트)."""
    def no_translation(text, target, llm=None):
        raise AssertionError("the operator wrote this reminder in Korean — it must not be re-translated")

    monkeypatch.setattr("src.llm.translate.translate_to", no_translation)
    monkeypatch.setitem(TEMPLATES, "followup_reminder_ko", "안녕하세요,\n\n지난 메일에 이어 연락드립니다.\n\n감사합니다.")
    conv = _ticket(db, sent_days_ago=4, language="ko")
    fs.run_followup_sequence_once()
    reminder = _outgoing(db, conv)[1]
    assert reminder.body == TEMPLATES["followup_reminder_ko"]
    assert (reminder.language, reminder.target_language) == ("ko", "ko")


def test_three_five_seven_and_then_concluded(db):
    """리마인더 둘 뒤 7일 — **Concluded** 로 닫는다(2026-09-30 운영자 지시: 「리마인더 메일 다 끝나면
    concluded로 가야하는데」). 09-17 에는 Closed Lost 였다 — 답이 없어 끝난 문의는 진 건이 아니다."""
    from src.agents.stage_sync import LOCAL_STAGE_TO_SETTING
    from src.api.routes import customer_ops

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
    assert _stage(db, conv) == "closed"
    with db() as session:
        assert session.get(Conversation, conv).followup_closed_at is not None
    # 허브스팟 · 워크북도 같은 단계로 간다 — 그 키의 허브스팟 단계가 Concluded(1404814097)다.
    assert customer_ops._sync_stage.await_args.args[1] == "closed"
    assert LOCAL_STAGE_TO_SETTING["closed"] == "HUBSPOT_TICKET_STAGE_CLOSED"


def test_a_reply_after_the_automatic_close_brings_the_ticket_back_red(db):
    """7일이 지나 닫았어도 답장이 오면 협의 중으로 — 그 티켓은 빨갛게 선다(운영자)."""
    conv = _ticket(db, sent_days_ago=30)
    with db() as session:
        c = session.get(Conversation, conv)
        c.stage, c.followup_closed_at = "closed", _now() - timedelta(days=1)
        session.add(CustomerInteraction(contact_id=c.contact_id, conversation_id=conv, channel="email",
                                        direction="inbound", summary="sorry, back now",
                                        external_id="hubspot:conv:77", happened_at=_now()))
        session.commit()
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "negotiation"
    with db() as session:
        view = fs.view(session.get(Conversation, conv), [])
    assert view["state"] == "revived"


def test_a_reply_after_the_close_revives_even_when_the_operator_answered_in_the_same_round(db):
    """고객 답장(10:01)과 운영자의 허브스팟 화면 회신(10:05)이 한 회차에 같이 들어왔다 — 기준만 보면
    운영자 회신이 새 기준이 되어 고객 답장이 안 보이고 종결에 남는다. 닫은 뒤의 연락은 되살린다."""
    conv = _ticket(db, sent_days_ago=30)
    with db() as session:
        c = session.get(Conversation, conv)
        c.stage, c.followup_closed_at = "closed", _now() - timedelta(days=1)
        session.add(CustomerInteraction(contact_id=c.contact_id, conversation_id=conv, channel="이메일",
                                        direction="inbound", summary="sorry, back now",
                                        external_id="hubspot:conv:77", happened_at=_now() - timedelta(minutes=9)))
        session.add(CustomerInteraction(contact_id=c.contact_id, conversation_id=conv, channel="이메일",
                                        direction="outgoing", summary="Great to hear from you!",
                                        external_id="hubspot:conv:78", happened_at=_now() - timedelta(minutes=5)))
        session.commit()
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "negotiation"


@pytest.mark.parametrize("stage, auto_closed", [
    ("closed_lost", False),  # 사람이 Closed Lost 로
    ("closed", False),       # 사람이 Concluded 로 — 시퀀스가 닫은 것과 단계는 같다, 표시(`followup_closed_at`)가 가른다
    ("closed_lost", True),   # 시퀀스가 종결한 뒤 사람이 Closed Lost 로 옮겼다 — 사람의 결정이다
])
def test_a_ticket_a_person_closed_is_not_revived(db, stage, auto_closed):
    conv = _ticket(db, sent_days_ago=30, stage=stage)
    with db() as session:
        c = session.get(Conversation, conv)
        if auto_closed:
            c.followup_closed_at = _now() - timedelta(days=1)
        session.add(CustomerInteraction(contact_id=c.contact_id, conversation_id=conv, channel="email",
                                        direction="inbound", summary="hi", external_id="hubspot:conv:78",
                                        happened_at=_now()))
        session.commit()
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == stage


# ---- 시퀀스는 한 번만 한다 — 닫기는 한 회차에 한 번, 되살리기는 닫은 한 번에 한 번 (2026-09-30) ----------

def _auto_concluded(db) -> int:
    """3·5·7 을 끝까지 돌린 티켓 — 시퀀스가 Concluded 로 닫았다."""
    conv = _ticket(db, sent_days_ago=30)
    fs.run_followup_sequence_once()
    _mark_sent(db, _outgoing(db, conv)[1].id, days_ago=13)
    fs.run_followup_sequence_once()
    _mark_sent(db, _outgoing(db, conv)[2].id, days_ago=7.1)
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "closed"
    return conv


def _customer_writes(db, conv, external_id="hubspot:conv:back-1"):
    with db() as session:
        c = session.get(Conversation, conv)
        session.add(CustomerInteraction(contact_id=c.contact_id, conversation_id=conv, channel="이메일",
                                        direction="inbound", summary="sorry, back now",
                                        external_id=external_id, happened_at=_now()))
        session.commit()


def test_a_revived_ticket_a_person_concludes_again_stays_concluded(db):
    """닫고 → 고객이 답해 되살아나고 → 사람이 다시 Concluded 로 옮겼다. 그 뒤 스윕이 **옛 답장**으로 또 협의
    중으로 돌리면, 사람이 옮길 때마다 허브스팟·워크북까지 되돌린다 — 새 연락이 없어도(2026-09-30 검증이 네
    방향에서 따로 재현). 닫는 단계가 Concluded 가 되면서 그 구멍이 사람이 문의를 끝낼 때 쓰는 단계로 왔다."""
    from src.api.routes import customer_ops
    from src.api.routes.customer_ops import _set_conversation_stage

    conv = _auto_concluded(db)
    _customer_writes(db, conv)
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "negotiation"
    with db() as session:
        assert fs.view(session.get(Conversation, conv), _outgoing(db, conv))["state"] == "revived"

    _set_conversation_stage(conv, "closed")  # 보드에서 Concluded 로 — 사람의 결정
    customer_ops._sync_stage.reset_mock()
    fs.run_followup_sequence_once()
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "closed"
    assert customer_ops._sync_stage.await_count == 0, "허브스팟·워크북을 다시 되돌렸습니다"
    with db() as session:
        # 시퀀스는 이 티켓에서 손을 뗐다 — 「자동으로 닫았습니다」 배너도 안 선다.
        assert fs.view(session.get(Conversation, conv), _outgoing(db, conv)) is None


def test_a_reopened_ticket_is_not_closed_again_until_a_new_mail(db):
    """자동 종결 뒤 사람이 Contacted 로 되돌렸다(다시 열었다). 기준도 리마인더도 그대로라 닫을 때가 지났다고
    읽히지만, 다음 스윕이 10분 안에 다시 닫으면 사람의 결정을 기계가 되돌린다. 새 메일이 나가면 처음부터."""
    from src.api.routes import customer_ops
    from src.api.routes.customer_ops import _set_conversation_stage

    conv = _auto_concluded(db)
    _set_conversation_stage(conv, "meeting_link_sent")
    customer_ops._sync_stage.reset_mock()
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "meeting_link_sent"
    assert customer_ops._sync_stage.await_count == 0
    with db() as session:
        view = fs.view(session.get(Conversation, conv), _outgoing(db, conv))
    assert view["state"] == "reopened" and view["reminder"] == "Reminder Sent 2"

    with db() as session:  # 운영자가 다시 메일을 보냈다 — 그 메일이 새 기준이다
        session.add(Message(conversation_id=conv, direction="outgoing", status="sent", body="one more idea",
                            to_address="buyer@example.com", subject="RE: Custom quote", language="en",
                            target_language="en", sent_at=_now()))
        session.commit()
    with db() as session:
        view = fs.view(session.get(Conversation, conv), _outgoing(db, conv))
    assert view["state"] == "send_1" and view["reminder"] == "Pending"


def test_moving_an_auto_closed_ticket_to_negotiating_by_hand_is_not_a_revival(db):
    """빨간 표시는 「닫았는데 고객이 돌아왔다」다. 사람이 손으로 협의 중에 옮긴 것까지 빨갛게 서면 그 표시가
    아무것도 말하지 않는다."""
    from src.api.routes.customer_ops import _set_conversation_stage

    conv = _auto_concluded(db)
    _set_conversation_stage(conv, "negotiation")
    with db() as session:
        c = session.get(Conversation, conv)
        assert not fs.revived_by_sequence(c)
        assert fs.view(c, _outgoing(db, conv)) is None


def test_a_closed_card_keeps_its_count_after_the_operator_writes_again(db):
    """닫힌 티켓에 운영자가 메일을 한 통 보내면 그 메일이 새 기준이 되어, 지금 기준으로 세는 칩이 닫힌 카드에
    「Pending」을 그렸다. 닫힌 · 되살아난 티켓의 칩은 언제나 「두 번 재촉했다」다."""
    conv = _auto_concluded(db)
    with db() as session:
        session.add(Message(conversation_id=conv, direction="outgoing", status="sent", body="one more idea",
                            to_address="buyer@example.com", subject="RE: Custom quote", language="en",
                            target_language="en", sent_at=_now()))
        session.commit()
    with db() as session:
        view = fs.view(session.get(Conversation, conv), _outgoing(db, conv))
    assert view["state"] == "closed" and view["reminder"] == "Reminder Sent 2"


def test_a_ticket_a_person_moved_before_the_sweep_saw_the_reply_is_not_revived_later(db):
    """자동 종결 → 고객이 답했는데 스윕보다 사람이 먼저 협의 중으로 옮겼다(서비스가 자는 동안이 그렇다) → 나중에
    사람이 끝냈다. 되살렸다는 표시가 안 적혔으니 스윕이 그 옛 답장으로 또 되살렸다. 닫은 뒤 단계가 한 번이라도
    움직였으면 시퀀스는 손을 뗀다(`followup_released_at`)."""
    from src.api.routes import customer_ops
    from src.api.routes.customer_ops import _set_conversation_stage

    conv = _auto_concluded(db)
    _customer_writes(db, conv)
    _set_conversation_stage(conv, "negotiation")  # 스윕보다 먼저, 보드에서
    _set_conversation_stage(conv, "closed")       # 이야기가 끝나 Concluded 로
    customer_ops._sync_stage.reset_mock()
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "closed"
    assert customer_ops._sync_stage.await_count == 0, "허브스팟·워크북을 다시 되돌렸습니다"


def test_any_stage_write_after_the_automatic_close_counts_but_an_echo_does_not(db):
    """단계를 쓰는 곳은 여럿이다(보드 · 허브스팟 동기화 · 워크북 · 백필 · 발송 워커). 보드 함수에만 달면 허브스팟
    에서 옮긴 티켓이 빠진다 — 그래서 대입 자체에서 잡는다. 우리가 옮긴 것의 메아리(같은 값)는 움직임이 아니다."""
    conv = _auto_concluded(db)
    with db() as session:  # 허브스팟이 우리 이동을 되돌려 알린다 — 같은 값
        session.get(Conversation, conv).stage = "closed"
        session.commit()
    with db() as session:
        assert fs.closed_by_sequence(session.get(Conversation, conv))

    with db() as session:  # 허브스팟 동기화처럼 행에 바로 쓴다 — 사람이 거기서 옮겼다
        session.get(Conversation, conv).stage = "negotiation"
        session.commit()
    with db() as session:
        c = session.get(Conversation, conv)
        assert c.followup_released_at is not None and not fs.revived_by_sequence(c)
        c.stage = "closed"
        session.commit()
    _customer_writes(db, conv)
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "closed"


def test_a_second_automatic_close_counts_afresh(db):
    """다시 열었다가 새 회차가 끝까지 가서 또 닫혔다 — 그 닫기는 새로 센다. 다시 연 기록이 남아 있으면 이번
    닫기 뒤의 답장이 안 되살린다."""
    from src.api.routes.customer_ops import _set_conversation_stage

    conv = _auto_concluded(db)
    _set_conversation_stage(conv, "meeting_link_sent")
    fs._close(conv)
    with db() as session:
        assert fs.closed_by_sequence(session.get(Conversation, conv))
    _customer_writes(db, conv)
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "negotiation"


# ---- 상태는 행에서 읽는다 ------------------------------------------------------

def _msg(id_, *, variant=None, status="sent", days_ago=0.0, made_days_ago=None):
    """만든 시각은 따로 안 주면 나간 시각과 같다 — 리마인더는 만든 뒤 1분 안에 나간다."""
    at = _now() - timedelta(days=days_ago)
    made = _now() - timedelta(days=made_days_ago) if made_days_ago is not None else at
    return Message(id=id_, direction="outgoing", status=status, prompt_variant=variant, body="x",
                   sent_at=at if status == "sent" else None, created_at=made)


def test_a_new_human_reply_restarts_the_clock_from_reminder_one():
    """R1 뒤에 사람이 또 보내면 다음은 R2 가 아니라 R1 이다 — 사람 메일 사흘 뒤 「닫겠습니다」가 안 나간다."""
    base, reminders = fs.sequence_state([
        _msg(1, days_ago=10), _msg(2, variant=fs.REMINDER_1, days_ago=6), _msg(3, days_ago=1),
    ])
    assert base.id == 3 and reminders == {}
    assert fs.next_step(base, reminders)[0] == "send_1"


def test_a_reply_redrafted_before_reminder_one_but_sent_after_it_restarts_at_reminder_one():
    """사람 초안을 R1 **전에** 만들고(실패 → 다시 쓰기) R1 **뒤에** 보냈다. id 로 재면 R1 이 새 기준
    「뒤」로 세어져 나흘 만에 「닫겠습니다」가 나갔다 — 기준은 나간 시각, 리마인더는 만든 시각으로 잰다."""
    base, reminders = fs.sequence_state([
        _msg(1, days_ago=10),
        _msg(3, variant=fs.REMINDER_1, days_ago=6),
        _msg(2, days_ago=1, made_days_ago=8),  # id 는 R1 보다 작지만 나간 것은 R1 뒤
    ])
    assert base.id == 2 and reminders == {}
    assert fs.next_step(base, reminders)[0] == "send_1"


def test_a_draft_abandoned_before_our_last_mail_does_not_hold_the_reminder(db):
    """「메일 발송」으로 열어만 두고 허브스팟 화면에서 답했다 — 그 초안은 쓰는 중이 아니라 버려진 것이다.
    예전에는 그런 행 하나가 그 티켓의 리마인더를 영원히 막았고 화면에는 표시가 없었다."""
    conv = _ticket(db, sent_days_ago=4)
    with db() as session:
        session.add(Message(conversation_id=conv, direction="outgoing", status="pending_approval",
                            body="old", prompt_variant="manual", created_at=_now() - timedelta(days=5)))
        session.commit()
    fs.run_followup_sequence_once()
    assert _outgoing(db, conv)[-1].prompt_variant == fs.REMINDER_1


def test_the_ticket_says_a_draft_is_holding_the_reminder(db):
    conv = _ticket(db, sent_days_ago=4)
    with db() as session:
        draft = Message(conversation_id=conv, direction="outgoing", status="pending_approval",
                        body="writing", prompt_variant="manual")
        session.add(draft)
        session.commit()
        draft_id = draft.id
    with db() as session:
        view = fs.view(session.get(Conversation, conv), _outgoing(db, conv))
    assert view["state"] == "send_1" and view["held_by_draft"] == draft_id


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
    # 언어별 행도 발송 경로가 이름으로 찾는다 — 지우면 그 언어 고객은 번역본을 받는다.
    assert all(is_code_resolved(f"{key}_ko") for key in fs.TEMPLATE_KEYS.values())
    assert not is_code_resolved("followup_reminder_old")


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


# ---- 몇 차까지 나갔나 (2026-09-22 운영자 지시: 「리마인더 센트 기본적으로 떠있게(Pendding,
# Reminder Sent 1 , Reminder Sent 2)」) ---------------------------------------------------

def test_the_chip_is_the_highest_reminder_that_actually_went_out():
    """순수 함수. 글자는 운영자가 적은 그대로이고, 한 통도 안 나갔으면 「Pending」."""
    base = _msg(1, days_ago=30)
    assert fs.reminder_status([]) == "Pending"
    assert fs.reminder_status([base]) == "Pending"
    assert fs.reminder_status([base, _msg(2, variant=fs.REMINDER_1, days_ago=20)]) == "Reminder Sent 1"
    assert fs.reminder_status([base, _msg(2, variant=fs.REMINDER_1, days_ago=20),
                               _msg(3, variant=fs.REMINDER_2, days_ago=10)]) == "Reminder Sent 2"
    # SAFE 모드로 「나간」 것은 고객이 받은 것이 없다 — `next_step` 과 같은 기준.
    assert fs.reminder_status(
        [base, _msg(2, variant=fs.REMINDER_1, status="test_sent", days_ago=20)]) == "Pending"


def test_the_ticket_says_which_reminders_are_done(db):
    conv = _ticket(db, sent_days_ago=30)
    with db() as session:
        # 시퀀스가 도는 순간부터 칩이 선다 — 「기본적으로 떠있게」.
        assert fs.view(session.get(Conversation, conv), _outgoing(db, conv))["reminder"] == "Pending"
    fs.run_followup_sequence_once()
    first = _outgoing(db, conv)[1]
    with db() as session:
        assert fs.view(session.get(Conversation, conv), _outgoing(db, conv))["reminder"] == "Pending"

    _mark_sent(db, first.id, days_ago=5.1)
    with db() as session:
        assert fs.view(session.get(Conversation, conv), _outgoing(db, conv))["reminder"] \
            == "Reminder Sent 1"

    fs.run_followup_sequence_once()
    _mark_sent(db, _outgoing(db, conv)[2].id, days_ago=1)
    with db() as session:
        assert fs.view(session.get(Conversation, conv), _outgoing(db, conv))["reminder"] \
            == "Reminder Sent 2"


@pytest.mark.parametrize("status", ["approved", "send_failed", "test_sent"])
def test_a_reminder_that_never_reached_the_customer_is_not_done(db, status):
    """`next_step` 이 「이미 보냈다」로 쓰는 그 조건이어야 한다.

    `test_sent` 는 SAFE 모드로 「나간」 것이라 고객이 받은 것이 없다. `sent_at` 만 보면 시퀀스는
    `stalled` 인데 화면만 완료라고 적는다.
    """
    conv = _ticket(db, sent_days_ago=30)
    fs.run_followup_sequence_once()
    with db() as session:
        row = _outgoing(db, conv)[1]
        held = session.get(Message, row.id)
        held.status, held.sent_at = status, _now() - timedelta(days=1)
        session.commit()
    with db() as session:
        assert fs.view(session.get(Conversation, conv), _outgoing(db, conv))["reminder"] == "Pending"


def test_a_closed_ticket_still_says_it_was_chased_twice(db):
    """닫힌 티켓이야말로 「두 번 재촉하고 닫았다」가 적혀 있어야 하는 자리다."""
    conv = _ticket(db, sent_days_ago=30)
    fs.run_followup_sequence_once()
    _mark_sent(db, _outgoing(db, conv)[1].id, days_ago=5.1)
    fs.run_followup_sequence_once()
    _mark_sent(db, _outgoing(db, conv)[2].id, days_ago=7.1)
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "closed"
    with db() as session:
        view = fs.view(session.get(Conversation, conv), _outgoing(db, conv))
    assert view["state"] == "closed"
    assert view["reminder"] == "Reminder Sent 2"
    # 배너가 적는 이름은 파이프라인 이름표 한 곳에서 — 글자로 박지 않는다.
    assert view["closes_to"] == "Concluded"


# ---- 콘솔 밖에서 나간 회신도 시계를 돌린다 (2026-09-28 운영자 보고: 「contacted 에 온지 4일이
# 지났는데 리마인더 발송이 안되었어」 — 424·425 는 허브스팟 받은편지함 화면에서 답했다) ------------

_HUBSPOT_REPLY = {
    "id": "hs-ui-1",
    "channelId": "1002",
    "channelAccountId": "3114216464",
    "direction": "OUTGOING",
    "type": "MESSAGE",
    "subject": "Seu curso de 130 vídeos: algumas perguntas",
    "senders": [{"actorId": "A-1", "senderField": "FROM",
                 "deliveryIdentifier": {"type": "HS_EMAIL_ADDRESS", "value": "perso.ai@estsoft.com"}}],
    "recipients": [
        {"recipientField": "TO", "deliveryIdentifier": {"type": "HS_EMAIL_ADDRESS", "value": "buyer@example.com"}},
        {"recipientField": "CC", "deliveryIdentifier": {"type": "HS_EMAIL_ADDRESS", "value": "untae@estsoft.com"}},
    ],
}


def _reminders_of(factory, conv_id):
    return [m for m in _outgoing(factory, conv_id) if m.prompt_variant in fs.REMINDER_VARIANTS]


def _answered_outside(factory, *, days_ago: float, external_id: str = "hubspot:conv:hs-ui-1",
                      channel: str = "이메일", context: str | None = None,
                      body: str = "Olá Filipe, obrigado pelo contato.") -> int:
    """New 로 들어와 **콘솔 밖에서** 답한 티켓 — `messages` 에는 문의 한 줄과 밀려난 초안뿐이다."""
    with factory() as session:
        contact = Contact(normalized_email="buyer@example.com", email="buyer@example.com",
                          full_name="Buyer")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="meeting_link_sent", hubspot_ticket_id="T-9",
                            inquiry_subject="Preciso traduzir meu curso", inquiry_language="en")
        session.add(conv)
        session.flush()
        session.add(Message(conversation_id=conv.id, direction="inbound", body="Work email: …",
                            created_at=_now() - timedelta(days=days_ago + 1)))
        session.add(Message(conversation_id=conv.id, direction="outgoing", status="superseded",
                            body="AI draft nobody sent"))
        session.add(CustomerInteraction(
            contact_id=contact.id, conversation_id=conv.id, channel=channel, direction="outgoing",
            subject=_HUBSPOT_REPLY["subject"], summary=body, handler="perso.ai@estsoft.com",
            context=context, external_id=external_id, happened_at=_now() - timedelta(days=days_ago),
        ))
        session.commit()
        return conv.id


@pytest.fixture()
def hubspot_reply(monkeypatch):
    """허브스팟 스레드 읽기를 가짜로 — 리마인더를 만들기 직전에 그 회신의 받는 사람·참조를 읽는다."""
    from src.agents import ticket_history

    class _Client:
        async def close(self):
            return None

        async def _live_email_channel_accounts(self):
            return [{"id": "3114216464", "deliveryIdentifier": {"value": "perso.ai@estsoft.com"}}]

    calls = []

    async def threads(client, ticket_id):
        calls.append(ticket_id)
        return ["th-1"]

    async def messages(client, thread_id):
        return [dict(_HUBSPOT_REPLY)]

    monkeypatch.setattr("src.integrations.hubspot.HubSpotClient", _Client)
    monkeypatch.setattr(ticket_history, "_live_thread_ids", threads)
    monkeypatch.setattr(ticket_history, "_thread_messages", messages)
    monkeypatch.setattr("src.llm.language.detect_language", lambda text, llm=None, default="en": "pt")
    monkeypatch.setattr("src.llm.translate.translate_to",
                        lambda text, target, llm=None: "Olá, dando continuidade à minha última mensagem.")
    return calls


def test_a_reply_sent_from_the_hubspot_inbox_starts_the_clock(db, hubspot_reply):
    """424 그대로: 허브스팟 화면에서 답하고 나흘 — 리마인더 1 이 **그 회신을 베껴** 선다."""
    conv = _answered_outside(db, days_ago=4)
    fs.run_followup_sequence_once()
    fs.run_followup_sequence_once()  # 두 번 돌려도 하나
    reminders = _reminders_of(db, conv)
    assert len(reminders) == 1
    reminder = reminders[0]
    assert reminder.prompt_variant == fs.REMINDER_1
    assert reminder.status == "approved" and reminder.approved_by == fs.APPROVER
    # 같은 사람이 같은 문으로 — 받는 사람 · 참조 · 발신 계정은 허브스팟의 그 메시지에서.
    assert reminder.to_address == "buyer@example.com"
    assert reminder.cc_addresses == "untae@estsoft.com"
    assert reminder.channel_account_id == "3114216464"
    assert reminder.subject == "RE: Seu curso de 130 vídeos: algumas perguntas"
    # 그 회신은 서명 카드가 없었다(본문에 손으로 쓴 맺음말) — 남의 카드를 붙이지 않는다.
    assert reminder.signature_key is None
    # 문의 언어(`en`, 폼의 영어 칸 이름 탓)가 아니라 **우리가 답한 언어**로.
    assert (reminder.language, reminder.target_language) == ("pt", "pt")
    assert reminder.body.startswith("Olá")
    assert hubspot_reply == ["T-9"]


def test_the_ticket_shows_pending_for_a_hubspot_inbox_reply(db):
    """배너·보드 칩이 스윕과 같은 자를 써야 한다 — 안 그러면 리마인더는 나가는데 화면은 조용하다."""
    conv = _answered_outside(db, days_ago=1)
    with db() as session:
        outside = fs.outside_replies(session, [conv])
        view = fs.view(session.get(Conversation, conv), _outgoing(db, conv), outside.get(conv, ()))
    assert view["state"] == "send_1" and view["reminder"] == "Pending"
    assert view["due"] > _now()
    # 그 입력 없이 부르면 예전처럼 None — 호출부가 빠뜨리면 칩이 사라진다는 뜻이다.
    with db() as session:
        assert fs.view(session.get(Conversation, conv), _outgoing(db, conv)) is None


def test_nothing_goes_out_three_days_before_a_hubspot_inbox_reply(db, hubspot_reply):
    conv = _answered_outside(db, days_ago=2.9)
    fs.run_followup_sequence_once()
    assert not _reminders_of(db, conv)


def test_a_hubspot_inbox_reply_before_the_start_date_never_starts(db, monkeypatch, hubspot_reply):
    """「새 메일부터」는 콘솔 밖 회신에도 같다 — 스위치를 켜기 전 회신은 시계를 안 돌린다."""
    monkeypatch.setattr(settings, "FOLLOWUP_SEQUENCE_SINCE", (_now() - timedelta(days=3)).strftime("%Y-%m-%d"))
    conv = _answered_outside(db, days_ago=10)
    fs.run_followup_sequence_once()
    assert not _reminders_of(db, conv)


@pytest.mark.parametrize("channel", ["채팅", "폼"])
def test_a_chat_or_bot_answer_is_not_a_reply_to_follow_up(db, hubspot_reply, channel):
    """425 는 챗봇이 먼저 답했다 — 봇 답이나 채팅 뒤에 「지난 메일에 이어」는 거짓이다."""
    conv = _answered_outside(db, days_ago=4, channel=channel)
    fs.run_followup_sequence_once()
    assert not _reminders_of(db, conv)
    with db() as session:
        assert fs.outside_replies(session, [conv]) == {}


def test_a_hand_written_record_is_not_a_reply_to_follow_up(db, hubspot_reply):
    """콘솔에 손으로 적은 「발신」 기록(전화·미팅)은 열쇠가 없다 — 메일이 아니다."""
    conv = _answered_outside(db, days_ago=4)
    with db() as session:
        row = session.query(CustomerInteraction).filter_by(conversation_id=conv).one()
        row.external_id = None
        session.commit()
    fs.run_followup_sequence_once()
    assert not _reminders_of(db, conv)


def test_a_cs_mail_is_not_a_reply_to_follow_up(db, monkeypatch, hubspot_reply):
    """CS 주소(`NON_SALES_SENDER_ADDRESSES`)의 안내는 영업이 「지난 메일에 이어」 재촉할 메일이 아니다 —
    첫 회신 판정 · 답장 기준선과 같은 자(`history_view.is_sales_email`)로 잰다(2026-10-06)."""
    monkeypatch.setattr(settings, "NON_SALES_SENDER_ADDRESSES", "support@perso.ai")
    conv = _answered_outside(db, days_ago=4)
    with db() as session:
        session.query(CustomerInteraction).filter_by(conversation_id=conv).one().handler = "support@perso.ai"
        session.commit()
    fs.run_followup_sequence_once()
    assert not _reminders_of(db, conv)
    with db() as session:
        assert fs.outside_replies(session, [conv]) == {}


@pytest.mark.parametrize("channel", ["이메일", "email"])
def test_both_spellings_of_the_email_channel_count(db, channel):
    """채널 말의 두 철자 — 스레드 수집기는 「이메일」, CRM 가져오기는 `email`(열쇠는 같은 스레드 메시지 id)."""
    conv = _answered_outside(db, days_ago=1, channel=channel)
    with db() as session:
        assert [r.external_id for r in fs.outside_replies(session, [conv])[conv]] == ["hubspot:conv:hs-ui-1"]


def test_if_the_hubspot_reply_cannot_be_read_nothing_goes_out(db, monkeypatch, hubspot_reply):
    """참조를 잃은 채 나가느니 다음 회차 — `_recheck` 와 같은 fail closed."""
    from src.agents import ticket_history

    async def gone(client, thread_id):
        return []

    monkeypatch.setattr(ticket_history, "_thread_messages", gone)
    conv = _answered_outside(db, days_ago=4)
    fs.run_followup_sequence_once()
    assert not _reminders_of(db, conv)


def test_a_personal_mailbox_reply_is_followed_from_that_mailbox(db, hubspot_reply):
    """개인 사서함에서 직접 보낸 메일이 기준이면 리마인더도 그 사서함에서(운영자 결정 ④)."""
    conv = _answered_outside(db, days_ago=4, external_id="gmail:abc",
                             context="untae@estsoft.com 개인 메일함")
    fs.run_followup_sequence_once()
    reminder = _reminders_of(db, conv)[0]
    assert reminder.channel_account_id == "gmail:untae@estsoft.com"
    assert reminder.to_address == "buyer@example.com"
    assert reminder.cc_addresses is None
    assert hubspot_reply == []  # 허브스팟 스레드는 안 읽는다


def test_a_dead_personal_mailbox_holds_a_reminder_that_would_go_through_it(db, hubspot_reply):
    from src.db.models import MailboxAccount

    conv = _answered_outside(db, days_ago=4, external_id="gmail:abc",
                             context="Untae@estsoft.com 개인 메일함")
    with db() as session:
        session.add(MailboxAccount(email="untae@estsoft.com", encrypted_payload="x", enabled=True,
                                   last_error="invalid_grant"))
        session.commit()
    fs.run_followup_sequence_once()
    assert not _reminders_of(db, conv)


def test_the_hubspot_copy_of_our_own_mail_is_counted_once(db):
    """콘솔에서 보낸 회신과 리마인더는 수집기가 허브스팟에서 도로 가져온다. 그 사본이 새 기준이 되면
    리마인더를 보낼 때마다 시계가 처음으로 돌아가 **3일마다 1차가 다시 나간다.**"""
    conv = _ticket(db, sent_days_ago=30, hubspot_message_id="hs-base")
    fs.run_followup_sequence_once()
    first = _outgoing(db, conv)[1]
    _mark_sent(db, first.id, days_ago=5.1)
    with db() as session:
        session.get(Message, first.id).hubspot_message_id = "hs-r1"
        contact_id = session.get(Conversation, conv).contact_id
        for key, days in (("hs-base", 30), ("hs-r1", 5.1)):
            session.add(CustomerInteraction(
                contact_id=contact_id, conversation_id=conv, channel="이메일", direction="outgoing",
                summary="copy", external_id=f"hubspot:conv:{key}",
                happened_at=_now() - timedelta(days=days),
            ))
        session.commit()
    with db() as session:
        assert fs.outside_replies(session, [conv]) == {}
    fs.run_followup_sequence_once()
    assert _outgoing(db, conv)[-1].prompt_variant == fs.REMINDER_2


def test_the_copy_of_a_reminder_with_an_unknown_outcome_does_not_restart_the_clock(db):
    """허브스팟이 답을 못 줘 id 가 없는 리마인더(`delivery_unknown`)의 사본은 시각으로 알아본다 —
    못 알아보면 그 사본이 기준이 되어 사흘 뒤 1차가 **또** 나간다. 시퀀스는 멈춰 있어야 한다."""
    conv = _ticket(db, sent_days_ago=30)
    fs.run_followup_sequence_once()
    first = _outgoing(db, conv)[1]
    with db() as session:
        row = session.get(Message, first.id)
        row.status = "delivery_unknown"
        # 쿼터에 밀려 한참 뒤에 나갔다 — 시각 창으로 재면 놓친다. 본문은 같은 글이다.
        session.add(CustomerInteraction(
            contact_id=session.get(Conversation, conv).contact_id, conversation_id=conv,
            channel="이메일", direction="outgoing", summary=row.body,
            external_id="hubspot:conv:unknown", happened_at=row.created_at + timedelta(hours=3),
        ))
        session.commit()
    with db() as session:
        assert fs.outside_replies(session, [conv]) == {}
    base, reminders = fs.sequence_state(_outgoing(db, conv), ())
    assert fs.next_step(base, reminders) == ("stalled", None)


def test_a_human_mail_right_after_a_failed_reminder_restarts_the_clock(db):
    """리마인더가 400 으로 실패해 **아무것도 안 나갔고**, 운영자가 몇 분 뒤 허브스팟 화면에서 직접 썼다 —
    그 메일은 리마인더의 사본이 아니라 새 기준이다(실패한 행에는 사본이 있을 수 없다)."""
    conv = _ticket(db, sent_days_ago=30)
    fs.run_followup_sequence_once()
    first = _outgoing(db, conv)[1]
    with db() as session:
        row = session.get(Message, first.id)
        row.status = "send_failed"
        session.add(CustomerInteraction(
            contact_id=session.get(Conversation, conv).contact_id, conversation_id=conv,
            channel="이메일", direction="outgoing", summary="Hi, just checking you saw my note.",
            external_id="hubspot:conv:human", happened_at=row.created_at + timedelta(minutes=5),
        ))
        session.commit()
    with db() as session:
        outside = fs.outside_replies(session, [conv])
    base, reminders = fs.sequence_state(_outgoing(db, conv), outside.get(conv, ()))
    assert base.external_id == "hubspot:conv:human"
    assert fs.next_step(base, reminders)[0] == "send_1"


def test_confirming_an_unknown_human_send_later_does_not_resend_reminder_one(db):
    """운영자 회신이 결과를 모른 채 끝났고(`delivery_unknown`), 그 뒤 리마인더 1 이 나갔다. 며칠 뒤 복구
    화면에서 「나간 것 확인」을 누르면 `sent_at` 이 **확인한 때**로 찍힌다 — 나간 시각으로 재면 이미 나간
    리마인더 1 이 「기준 전」이 되어 1차가 또 나갔다. 기준은 사람 손을 떠난 때(승인)로 잰다."""
    conv = _ticket(db, sent_days_ago=10)
    with db() as session:
        human = Message(conversation_id=conv, direction="outgoing", status="sent", body="follow-up",
                        to_address="buyer@example.com", subject="RE: Custom quote", prompt_variant="manual",
                        created_at=_now() - timedelta(days=9), approved_at=_now() - timedelta(days=9),
                        sent_at=_now())  # 오늘 확인했다
        session.add(human)
        session.flush()
        session.add(Message(conversation_id=conv, direction="outgoing", status="sent", body="r1",
                            prompt_variant=fs.REMINDER_1, created_at=_now() - timedelta(days=5),
                            approved_at=_now() - timedelta(days=5), sent_at=_now() - timedelta(days=5)))
        session.commit()
    base, reminders = fs.sequence_state(_outgoing(db, conv), ())
    assert base.prompt_variant == "manual"
    assert fs.next_step(base, reminders)[0] == "send_2"


def test_the_inquiry_itself_is_not_a_reply_when_we_answered_before_it_was_logged(db, hubspot_reply):
    """허브스팟 화면에서 먼저 답했고, 서버가 자고 있어 문의 행은 몇 분 뒤에 접수됐다 — 그 행의 시각은
    고객이 쓴 때가 아니다. 답장으로 읽으면 고객이 한 마디도 안 했는데 Negotiating 으로 옮겨진다."""
    conv = _answered_outside(db, days_ago=4)
    with db() as session:
        inquiry = session.query(Message).filter_by(conversation_id=conv, direction="inbound").one()
        inquiry.created_at = _now() - timedelta(days=4) + timedelta(minutes=15)
        session.commit()
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "meeting_link_sent"
    assert _reminders_of(db, conv)


def test_a_human_reply_from_hubspot_after_reminder_one_restarts_the_clock(db, hubspot_reply):
    """콘솔 회신 → 리마인더 1 → 사람이 허브스팟 화면에서 또 답했다: 다음은 2차가 아니라 1차, 사흘 뒤."""
    conv = _ticket(db, sent_days_ago=30)
    fs.run_followup_sequence_once()
    first = _outgoing(db, conv)[1].id
    _mark_sent(db, first, days_ago=6)
    with db() as session:
        # 실제 리마인더는 만든 뒤 1분 안에 나간다 — 만든 시각도 그날로.
        session.get(Message, first).created_at = _now() - timedelta(days=6)
        session.add(CustomerInteraction(
            contact_id=session.get(Conversation, conv).contact_id, conversation_id=conv,
            channel="이메일", direction="outgoing", summary="Another thought on pricing…",
            external_id="hubspot:conv:hs-ui-1", happened_at=_now() - timedelta(days=1),
        ))
        session.commit()
    fs.run_followup_sequence_once()
    assert len(_outgoing(db, conv)) == 2  # 사람 메일 하루 뒤 — 아무것도 안 나간다
    with db() as session:
        outside = fs.outside_replies(session, [conv])
        view = fs.view(session.get(Conversation, conv), _outgoing(db, conv), outside.get(conv, ()))
    assert view["state"] == "send_1"


def _hubspot_recipients(monkeypatch, recipients):
    from src.agents import ticket_history

    async def messages(client, thread_id):
        return [dict(_HUBSPOT_REPLY, recipients=[
            {"recipientField": field, "deliveryIdentifier": {"type": "HS_EMAIL_ADDRESS", "value": address}}
            for field, address in recipients
        ])]

    monkeypatch.setattr(ticket_history, "_thread_messages", messages)


def test_a_forward_to_someone_else_is_never_chased(db, monkeypatch, hubspot_reply):
    """허브스팟 화면에서 파트너에게 전달한 메일도 「우리가 보낸 이메일」로 수집된다 — 그 TO 를 베끼면
    「지난 메일에 이어」와 「닫겠습니다」가 파트너에게 간다. 고객이 받는 사람에 없으면 안 보낸다."""
    _hubspot_recipients(monkeypatch, [("TO", "partner@vendor.com")])
    conv = _answered_outside(db, days_ago=4)
    fs.run_followup_sequence_once()
    assert not _reminders_of(db, conv)


def test_the_customer_is_always_the_recipient_and_the_rest_stay_in_copy(db, monkeypatch, hubspot_reply):
    """고객이 참조에만 있던 전체 회신 — 받는 사람은 고객, 나머지는 참조로 옮겨 독자를 지킨다."""
    _hubspot_recipients(monkeypatch, [("TO", "cto@example.com"), ("CC", "Buyer@Example.com"),
                                      ("CC", "untae@estsoft.com")])
    conv = _answered_outside(db, days_ago=4)
    fs.run_followup_sequence_once()
    reminder = _reminders_of(db, conv)[0]
    assert reminder.to_address == "buyer@example.com"
    assert reminder.cc_addresses == "cto@example.com, untae@estsoft.com"


def test_an_undetectable_reply_language_waits_instead_of_sending_english(db, monkeypatch, hubspot_reply):
    """판정 실패를 「영어」로 읽으면 스페인어로 답한 고객에게 영어가 간다 — 번역 실패처럼 다음 회차로."""
    monkeypatch.setattr("src.llm.language.detect_language", lambda text, llm=None, default="en": default)
    conv = _answered_outside(db, days_ago=4)
    fs.run_followup_sequence_once()
    assert not _reminders_of(db, conv)


def test_a_reply_the_last_check_brings_in_stops_the_reminder_it_was_about_to_make(db, monkeypatch, hubspot_reply):
    """스윕이 「기한 지남」으로 본 뒤, 보내기 직전 확인(`_recheck`)이 운영자가 방금 허브스팟 화면에서 보낸
    회신을 가져왔다 — 기준은 그 회신이다. 다시 안 재면 고객이 사람 메일 몇 분 뒤 「지난 메일에 이어」를 받는다."""
    from src.agents import ticket_history

    conv = _answered_outside(db, days_ago=4)

    async def fresh_reply(conversation_id):
        with db() as session:
            session.add(CustomerInteraction(
                contact_id=session.get(Conversation, conversation_id).contact_id,
                conversation_id=conversation_id, channel="이메일", direction="outgoing",
                summary="Olá de novo", external_id="hubspot:conv:hs-ui-2",
                happened_at=_now() - timedelta(minutes=2),
            ))
            session.commit()
        return 1

    monkeypatch.setattr(ticket_history, "sync_one_ticket", fresh_reply)
    fs.run_followup_sequence_once()
    assert not _reminders_of(db, conv)


def test_our_own_inbox_never_lands_in_the_reminder_copy(db, monkeypatch, hubspot_reply):
    """동료가 인박스 주소를 TO 에 넣고 전체 회신한 메일이 기준 — 안 빼면 고객이 받는 리마인더의 참조에
    보낸 주소 자신이 선다. 운영자가 참조에 둔 동료는 그대로."""
    _hubspot_recipients(monkeypatch, [("TO", "perso.ai@estsoft.com"), ("TO", "buyer@example.com"),
                                      ("CC", "untae@estsoft.com")])
    conv = _answered_outside(db, days_ago=4)
    fs.run_followup_sequence_once()
    assert _reminders_of(db, conv)[0].cc_addresses == "untae@estsoft.com"


def test_a_mail_the_last_check_brings_in_stops_the_automatic_close(db, monkeypatch):
    """닫을 차례에 보내기 직전 확인이 운영자가 방금 허브스팟 화면에서 보낸 메일을 가져왔다 — 우리가 방금
    메일을 보낸 티켓을 종결로 닫으면 안 된다."""
    from src.agents import ticket_history

    conv = _ticket(db, sent_days_ago=30)
    fs.run_followup_sequence_once()
    first = _outgoing(db, conv)[1].id
    _mark_sent(db, first, days_ago=13)
    fs.run_followup_sequence_once()
    second = _outgoing(db, conv)[2].id
    _mark_sent(db, second, days_ago=7.1)
    with db() as session:
        session.get(Message, first).created_at = _now() - timedelta(days=13)
        session.get(Message, second).created_at = _now() - timedelta(days=7.1)
        session.commit()

    async def fresh_mail(conversation_id):
        with db() as session:
            session.add(CustomerInteraction(
                contact_id=session.get(Conversation, conversation_id).contact_id,
                conversation_id=conversation_id, channel="이메일", direction="outgoing",
                summary="One more idea for you", external_id="hubspot:conv:hs-new",
                happened_at=_now() - timedelta(minutes=5),
            ))
            session.commit()
        return 1

    monkeypatch.setattr(ticket_history, "sync_one_ticket", fresh_mail)
    fs.run_followup_sequence_once()
    assert _stage(db, conv) == "meeting_link_sent"


def test_the_board_chip_reads_hubspot_inbox_replies_too(db, monkeypatch):
    """보드 카드와 티켓 배너는 같은 입력을 받는다(`ui_api._reminders`)."""
    from src.api.routes import ui_api

    monkeypatch.setattr("src.db.session.SessionLocal", db)
    conv = _answered_outside(db, days_ago=1)
    with db() as session:
        conversation = session.get(Conversation, conv)
        session.expunge(conversation)
    chips = ui_api._reminders([{"stage": "meeting_link_sent", "conversation": conversation}])
    assert chips == {conv: "Pending"}
