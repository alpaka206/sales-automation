"""운영자가 직접 쓰는 후속 회신 (2026-08-31 운영자 지시).

자동 초안은 New 티켓에만 생기고 한 번 나가면 다시 안 생깁니다. 그래서 그 뒤의 대화는 전부
허브스팟에서 사람이 했고, 우리 화면에는 무엇이 오갔는지가 남지 않았습니다. 이 라우트는
초안 한 줄을 세우고 **초안 기계에 넘깁니다**(2026-09-07), 그 뒤는 자동 초안과 완전히 같은
길입니다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db.models import Contact, Conversation, Message


@pytest.fixture()
def db_session_factory():
    """**StaticPool 이어야 합니다.** TestClient 는 라우트를 다른 스레드에서 돌리는데,
    기본 풀은 스레드마다 새 `:memory:` DB 를 주므로 표가 없는 DB 를 보게 됩니다."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def ticket(db_session_factory, monkeypatch):
    from src.api.routes import messages as messages_route

    monkeypatch.setattr(messages_route, "SessionLocal", db_session_factory)
    with db_session_factory() as session:
        contact = Contact(normalized_email="buyer@example.com", email="buyer@example.com",
                          full_name="Buyer")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="negotiation",
                            hubspot_ticket_id="T-1", inquiry_subject="Custom quote",
                            inquiry_language="en")
        session.add(conv)
        session.commit()
        return conv.id


def _client():
    return TestClient(app)


def test_it_hands_the_draft_to_the_same_machine_that_writes_the_first_one(
    ticket, db_session_factory
):
    """**빈 편집기가 아니라 쓰인 초안**입니다 (2026-09-07 운영자 지시).

    모델은 이 요청 안에서 안 부릅니다 — pro 티어 한 통이라 20~30초가 걸리고, 그동안 요청이
    열려 있으면 프록시가 끊습니다. `drafting` 으로 세워 두고 워커가 채웁니다. 화면은 그
    상태를 이미 그릴 줄 압니다(「초안 다시 쓰기」와 같은 상태).

    큐에 올릴 때 **그 메시지 id 를 실어야** 합니다. 안 실으면 워커가 새 초안을 따로 만들고,
    한 티켓에 회신이 둘이 됩니다.
    """
    from src.agents import inbound_worker

    with patch.object(inbound_worker, "enqueue_draft") as queued:
        with _client() as client:
            created = client.post(f"/tickets/{ticket}/reply")
    assert created.status_code == 200, created.text
    assert created.json()["created"] is True
    message_id = created.json()["message_id"]
    assert queued.call_args.args[1] == message_id

    with db_session_factory() as session:
        msg = session.get(Message, message_id)
    assert msg.status == "drafting"
    assert msg.prompt_variant == "manual"
    assert msg.direction == "outgoing"
    assert msg.to_address == "buyer@example.com"
    assert msg.body == ""
    # 나갈 언어는 문의가 정합니다 — 자동 초안과 같은 규칙입니다. `language` 를 같은 값으로
    # 두면 그 언어로 쓰는 한 번역 관문이 안 뜨고, 한국어로 쓰면 뜹니다.
    assert msg.target_language == "en" and msg.language == "en"
    assert msg.subject == "RE: Custom quote"


def test_a_second_press_opens_the_draft_that_is_already_open(ticket, db_session_factory):
    """티켓 하나에 초안이 둘이면 어느 것이 나갈지 화면만 봐서는 알 수 없습니다."""
    with _client() as client:
        first = client.post(f"/tickets/{ticket}/reply").json()
        second = client.post(f"/tickets/{ticket}/reply").json()

    assert second == {"message_id": first["message_id"], "created": False}
    with db_session_factory() as session:
        assert session.query(Message).count() == 1


def test_a_ticketless_inquiry_is_refused_before_the_operator_writes_anything(
    db_session_factory, monkeypatch
):
    """발송 경로가 티켓 스레드 회신 하나뿐이라, 티켓이 없으면 보낼 길이 없습니다.

    여기서 막지 않으면 운영자가 다 쓰고 발송을 누른 뒤에야 알게 됩니다.
    """
    from src.api.routes import messages as messages_route

    monkeypatch.setattr(messages_route, "SessionLocal", db_session_factory)
    with db_session_factory() as session:
        contact = Contact(normalized_email="x@example.com", email="x@example.com", full_name="X")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="won")
        session.add(conv)
        session.commit()
        conv_id = conv.id

    with _client() as client:
        refused = client.post(f"/tickets/{conv_id}/reply")
    assert refused.status_code == 400
    assert "허브스팟 티켓" in refused.json()["detail"]
    with db_session_factory() as session:
        assert session.query(Message).count() == 0


def test_a_queue_failure_leaves_a_writable_draft_not_a_stuck_one(ticket, db_session_factory):
    """**큐가 안 받아도 버튼은 듣습니다** — 예전처럼 빈 편집기가 열립니다.

    여기서 500 을 내면 「메일 발송」이 통째로 안 듣고, `drafting` 인 채로 두면 화면이
    영원히 「쓰는 중」입니다. 둘 다 초안을 직접 쓰는 것보다 나쁩니다.

    그리고 그 빈 초안은 **그대로 승인되지 않습니다** — 누르면 빈 메일이 나갑니다.
    """
    from src.agents import approval, inbound_worker

    with patch.object(inbound_worker, "enqueue_draft", side_effect=RuntimeError("큐 없음")):
        with _client() as client:
            message_id = client.post(f"/tickets/{ticket}/reply").json()["message_id"]

    with db_session_factory() as session:
        assert session.get(Message, message_id).status == "pending_approval"
    with patch.object(approval, "SessionLocal", db_session_factory):
        with pytest.raises(approval.ApprovalError, match="본문이 비어 있습니다"):
            approval.approve(message_id, approver="tester")


def test_the_console_can_actually_reach_it():
    """`/tickets` 가 브라우저 경로 목록에 없으면 로그인한 운영자가 토큰을 요구받습니다 —
    그리고 화면에는 저장 실패로만 보입니다."""
    from src.api.security import is_web_ui_path

    assert is_web_ui_path("/tickets/1/reply")


def test_the_send_button_sits_next_to_the_log_button():
    """**메일과 기록이 한자리에 섭니다** (2026-09-03 운영자 지시).

    이 티켓에 무언가를 남기는 길은 둘인데 화면의 양 끝에 떨어져 있었습니다 — 메일은 본문
    칼럼 맨 위의 네 줄짜리 안내 상자, 기록은 아래 카드의 「추가하기」. 그 상자는 New 를
    지난 티켓에서 초안이 없을 때마다 늘 서 있었고, 운영자 화면은 세로 640px 입니다.

    **모달을 따로 만들지 않았습니다.** 발송에는 번역 관문·서명·발신 주소·미리보기가 붙는데
    (`approval.translation_required`, `enforce_send_language`), 모달에 그 절반만 담으면
    외국어 티켓에서 운영자가 갇힙니다 — 번역 버튼이 없는 화면에서 발송이 거절당합니다.
    그래서 버튼은 옮기되 누르면 지금까지와 같은 편집기로 갑니다: **발송 화면은 하나입니다.**
    """
    import pathlib

    screen = pathlib.Path("frontend/src/screens/MessageDetail.tsx").read_text(encoding="utf-8")
    header = screen[screen.index('<div className="section-header__title">이 티켓의 기록</div>'):]
    header = header[: header.index("</div>\n              <div className=\"history-list\">")]
    assert "메일 발송" in header and "추가하기" in header, "두 버튼이 같은 머리에 있어야 합니다"
    assert "startReply" in header

    # 초안이 열려 있으면 안 그립니다 — 티켓 하나에 초안이 둘이면 어느 것이 나갈지 모릅니다.
    assert "!isDraftOpen" in header
    # 옛 안내 상자는 사라졌습니다.
    assert "이 티켓의 다음 답변을 여기서 쓸 수 있습니다" not in screen
