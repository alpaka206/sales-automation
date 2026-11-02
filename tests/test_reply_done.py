"""답변 대기의 [완료] — 고른 회신만 목록에서 뺀다 (2026-11-02 운영자).

행은 리드당 하나이고 상태 칩이 `남은 수/전체 수` 를 적는다. 뺀 회신의 열쇠는 그 회신 자체라, 고객이 새로 쓴 말은
저절로 다시 뜬다. 메일 · 기록 · 단계 · 허브스팟은 그대로이고 고객에게 아무것도 안 나간다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.agents.inbound import _answered_turn, done_reply_refs, threads_events
from src.api.routes import messages as messages_route
from src.api.routes.messages import _messages_list_context
from src.db.base import Base
from src.db.models import (
    Approval,
    Contact,
    Conversation,
    ConversationProgress,
    CustomerInteraction,
    DoneReply,
    Message,
)


def _ago(days: float) -> datetime:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)


def _ticket(session, email: str, stage: str = "negotiation") -> Conversation:
    contact = Contact(normalized_email=email, email=email, full_name=email)
    session.add(contact)
    session.flush()
    conv = Conversation(contact_id=contact.id, stage=stage, hubspot_ticket_id=f"T-{email}",
                        inquiry_subject=f"문의 {email}")
    session.add(conv)
    session.flush()
    return conv


def _we_wrote(session, conv, days: float) -> Message:
    at = _ago(days)
    msg = Message(conversation_id=conv.id, direction="outgoing", subject="RE: 답변", body="우리 답변",
                  status="sent", created_at=at, sent_at=at)
    session.add(msg)
    session.flush()
    return msg


def _customer_wrote(session, conv, days: float, body: str) -> str:
    item = CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv.id, channel="이메일",
                               direction="inbound", summary=body, external_id=f"hubspot:conv:{conv.id}-{body}",
                               happened_at=_ago(days))
    session.add(item)
    session.flush()
    return f"interaction:{item.id}"


def _row(email: str) -> dict | None:
    rows = _messages_list_context(status="awaiting")["messages"]
    return next((row for row in rows if row["email"] == email), None)


@pytest.fixture()
def db_session_factory():
    """라우트는 다른 스레드에서 돈다 — 메모리 DB 를 나눠 쓰도록 연결 하나로 묶는다(`test_insights.py` 와 같다)."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def console(db_session_factory, monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.main import app

    monkeypatch.setattr(messages_route, "SessionLocal", db_session_factory)
    with patch("src.db.session.SessionLocal", db_session_factory), TestClient(app) as client:
        yield client


@pytest.fixture()
def two_replies(db_session_factory, console):
    """우리가 답한 뒤 고객이 두 번 쓴 협의 중 티켓 — 질문 하나, 인사 하나."""
    with db_session_factory() as session:
        conv = _ticket(session, "lead@example.com")
        _we_wrote(session, conv, days=5)
        question = _customer_wrote(session, conv, 3, "견적서에 부가세가 포함되나요?")
        thanks = _customer_wrote(session, conv, 1, "네, 감사합니다!")
        session.commit()
        return conv.id, question, thanks


def _done(console, conv_id: int, *refs: str):
    return console.post(f"/tickets/{conv_id}/replies-done", data={"refs": ",".join(refs)})


def test_a_lead_is_one_row_that_counts_and_lists_its_replies(two_replies):
    row = _row("lead@example.com")
    assert (row["reply_open"], row["reply_total"]) == (2, 2)
    assert [reply["text"] for reply in row["replies"]] == ["견적서에 부가세가 포함되나요?", "네, 감사합니다!"]
    assert len(_messages_list_context(status="awaiting")["messages"]) == 1


def test_done_takes_out_only_that_reply_and_touches_nothing_else(console, two_replies, db_session_factory):
    conv_id, question, thanks = two_replies
    before = _row("lead@example.com")["waiting_since"]

    assert _done(console, conv_id, question).status_code == 200

    row = _row("lead@example.com")
    assert (row["reply_open"], row["reply_total"]) == (1, 2)
    assert [reply["ref"] for reply in row["replies"]] == [thanks]
    # 기다린 시간은 남은 회신 중 가장 오래된 것부터다.
    assert row["waiting_since"] > before
    with db_session_factory() as session:
        conv = session.get(Conversation, conv_id)
        assert conv.stage == "negotiation"
        assert session.query(CustomerInteraction).filter_by(conversation_id=conv_id).count() == 2


def test_when_all_are_done_the_row_goes_and_a_new_reply_brings_it_back(console, two_replies, db_session_factory):
    conv_id, question, thanks = two_replies
    assert _done(console, conv_id, question, thanks).status_code == 200
    assert _row("lead@example.com") is None

    with db_session_factory() as session:
        new = _customer_wrote(session, session.get(Conversation, conv_id), 0.1, "하나 더 여쭙니다")
        session.commit()

    row = _row("lead@example.com")
    assert (row["reply_open"], row["reply_total"]) == (1, 3)
    assert [reply["ref"] for reply in row["replies"]] == [new]


def test_a_mail_collected_late_is_not_swallowed(console, two_replies, db_session_factory):
    """누른 시각으로 재지 않는다 — 누르기 전에 썼는데 나중에 수집된 메일도 다시 뜬다."""
    conv_id, question, thanks = two_replies
    _done(console, conv_id, question, thanks)
    with db_session_factory() as session:
        _customer_wrote(session, session.get(Conversation, conv_id), 2, "늦게 수집된 메일")
        session.commit()
    assert [reply["text"] for reply in _row("lead@example.com")["replies"]] == ["늦게 수집된 메일"]


def test_our_next_mail_clears_the_row_and_the_ticket_list(console, two_replies, db_session_factory):
    conv_id, question, _thanks = two_replies
    _done(console, conv_id, question)
    with db_session_factory() as session:
        _we_wrote(session, session.get(Conversation, conv_id), 0.1)
        session.commit()
    assert _row("lead@example.com") is None
    ticket = messages_route._message_detail_context(conversation_id=conv_id)["ticket"]
    assert ticket["done_replies"] == []


def test_new_has_no_button_and_the_route_refuses_it(console, db_session_factory):
    with db_session_factory() as session:
        conv = _ticket(session, "new@example.com", stage="new")
        session.add(Message(conversation_id=conv.id, direction="inbound", body="문의", status="received"))
        session.add(Message(conversation_id=conv.id, direction="outgoing", subject="초안", body="초안",
                            status="pending_approval"))
        session.commit()
        conv_id = conv.id

    row = _row("new@example.com")
    assert row["replies"] == [] and row["reply_total"] == 0
    assert _done(console, conv_id, "message:1").status_code == 400
    with db_session_factory() as session:
        assert session.query(DoneReply).count() == 0


def test_only_the_replies_still_waiting_can_be_marked(console, two_replies, db_session_factory):
    conv_id, question, _thanks = two_replies
    assert _done(console, conv_id).status_code == 400
    assert _done(console, conv_id, "interaction:999999").status_code == 400
    # 우리 메일보다 앞의 말이나 다른 대화의 줄은 이 칸에 못 들어온다.
    with db_session_factory() as session:
        _we_wrote(session, session.get(Conversation, conv_id), 0.1)
        session.commit()
    assert _done(console, conv_id, question).status_code == 400
    with db_session_factory() as session:
        assert session.query(DoneReply).count() == 0


def test_pressing_twice_is_one_mark(console, two_replies, db_session_factory):
    conv_id, question, _thanks = two_replies
    assert _done(console, conv_id, question).status_code == 200
    assert _done(console, conv_id, question).status_code == 200
    with db_session_factory() as session:
        assert session.query(DoneReply).count() == 1


def test_the_ticket_says_who_and_undo_brings_it_back(console, two_replies, db_session_factory):
    conv_id, question, _thanks = two_replies
    _done(console, conv_id, question)

    lines = messages_route._message_detail_context(conversation_id=conv_id)["ticket"]["done_replies"]
    assert [line["ref"] for line in lines] == [question]
    assert lines[0]["done_by"] and lines[0]["done_at"]

    assert console.post(f"/tickets/{conv_id}/replies-done/undo", data={"ref": question}).status_code == 200
    assert _row("lead@example.com")["reply_open"] == 2
    assert messages_route._message_detail_context(conversation_id=conv_id)["ticket"]["done_replies"] == []
    assert console.post(f"/tickets/{conv_id}/replies-done/undo", data={"ref": question}).status_code == 400
    # 뺀 일과 되돌린 일은 처리 경과에 남는다 — 그 칸은 지우지 않는다.
    with db_session_factory() as session:
        details = [p.detail for p in session.query(ConversationProgress).filter_by(kind="reply_done")]
    assert len(details) == 2


def test_a_rejected_draft_already_decided_the_replies_before_it(two_replies, db_session_factory):
    conv_id, _question, _thanks = two_replies
    with db_session_factory() as session:
        conv = session.get(Conversation, conv_id)
        draft = Message(conversation_id=conv.id, direction="outgoing", subject="초안", body="초안",
                        status="rejected", prompt_variant="manual", created_at=_ago(0.8))
        session.add(draft)
        session.flush()
        session.add(Approval(message_id=draft.id, approver="web", action="reject", created_at=_ago(0.5)))
        new = _customer_wrote(session, conv, 0.1, "거절 뒤에 쓴 말")
        session.commit()
    row = _row("lead@example.com")
    assert (row["reply_open"], row["reply_total"]) == (1, 3)
    assert [reply["ref"] for reply in row["replies"]] == [new]


def test_the_draft_answers_what_is_left_not_the_reply_taken_out(two_replies, db_session_factory):
    """「감사합니다」만 뺐으면 앞의 질문에 답한다. 대화 맥락에서는 안 뺀다."""
    conv_id, question, thanks = two_replies
    with db_session_factory() as session:
        events = threads_events(session, [conv_id])[conv_id]

    assert _answered_turn(events, False).source_ref == thanks
    assert _answered_turn(events, False, {thanks}).source_ref == question
    # 전부 뺐는데 사람이 메일을 쓰면 지금처럼 가장 최근 말이다.
    assert _answered_turn(events, False, {question, thanks}).source_ref == thanks
    assert {thanks, question} <= {turn.source_ref for turn in events}


def test_the_draft_reads_the_marks_from_the_table(console, two_replies, db_session_factory):
    conv_id, _question, thanks = two_replies
    assert done_reply_refs(conv_id, factory=db_session_factory) == set()
    _done(console, conv_id, thanks)
    assert done_reply_refs(conv_id, factory=db_session_factory) == {thanks}


def test_a_customer_reply_taken_out_still_counts_as_a_reply_for_the_stage_rule(console, two_replies, db_session_factory):
    """단계를 옮기는 자는 그대로다 — [완료]는 목록의 일이고 「고객이 답장했다」는 사실은 안 바뀐다."""
    from src.agents.inbound import unanswered_customer_turns

    conv_id, question, thanks = two_replies
    _done(console, conv_id, question, thanks)
    with db_session_factory() as session:
        conv = session.get(Conversation, conv_id)
        events = threads_events(session, [conv_id])[conv_id]
        _baseline, turns = unanswered_customer_turns(events, conv.last_outgoing_at)
    assert {turn.source_ref for turn in turns} == {question, thanks}


def test_deleting_the_ticket_takes_its_marks_with_it(console, two_replies, db_session_factory, monkeypatch):
    from src.agents import hubspot_reconcile

    conv_id, question, _thanks = two_replies
    _done(console, conv_id, question)
    monkeypatch.setattr(hubspot_reconcile, "SessionLocal", db_session_factory)
    hubspot_reconcile.delete_conversation(conv_id, "T-lead@example.com")
    with db_session_factory() as session:
        assert session.query(DoneReply).count() == 0
