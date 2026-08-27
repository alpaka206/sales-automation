"""Operator recovery actions keep ambiguous delivery explicit and audited."""

from __future__ import annotations

import pathlib

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db.models import Contact, Conversation, Event, InboundJob, Message


@pytest.fixture()
def recovery_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    # 재시도는 이 라우트에서 시작해 ``inbound_worker.request_redraft`` 로 넘어가고 거기서
    # 진행 기록까지 남깁니다. 셋 다 자기 이름으로 SessionLocal 을 들고 있으므로(모듈이
    # ``from ... import SessionLocal`` 로 가져옵니다) 하나만 바꿔치면 나머지가 진짜 DB 를
    # 봅니다 — 그 상태로는 재시도가 404 로 떨어집니다.
    with (
        patch("src.api.routes.recovery.SessionLocal", factory),
        patch("src.agents.inbound_worker.SessionLocal", factory),
        patch("src.db.conversation_history.SessionLocal", factory),
    ):
        yield factory


def _seed(factory, status: str) -> tuple[int, int]:
    with factory() as session:
        contact = Contact(normalized_email="recovery@example.com", full_name="Recovery")
        session.add(contact)
        session.flush()
        conversation = Conversation(
            contact_id=contact.id, stage="new", hubspot_ticket_id="T-1"
        )
        session.add(conversation)
        session.flush()
        message = Message(
            conversation_id=conversation.id,
            direction="outgoing",
            body="reply",
            status=status,
        )
        session.add(message)
        session.flush()
        job = InboundJob(
            event_key="recovery-job",
            source="test",
            payload={"ticket_id": "T-1"},
            status="dead",
            attempts=8,
            last_error="boom",
        )
        session.add(job)
        session.commit()
        return message.id, job.id


def test_recovery_console_lists_failures(recovery_db) -> None:
    message_id, job_id = _seed(recovery_db, "send_failed")
    with TestClient(app) as client:
        payload = client.get("/api/ui/recovery").json()
    assert message_id in [row["id"] for row in payload["messages"]]
    assert job_id in [row["id"] for row in payload["inbound_jobs"]]


def test_old_recovery_url_redirects_into_the_operations_screen(recovery_db) -> None:
    """The console moved into /logs; bookmarks and the audit trail's links still work."""
    with TestClient(app) as client:
        response = client.get("/operations/recovery", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/logs?tab=recovery"


def test_operations_screen_defaults_to_the_recovery_tab(recovery_db) -> None:
    """Recovery is the tab with work on it; logs are for diagnosing what it shows.

    The default lives in the screen now (Simple.tsx reads ?tab, defaulting to recovery);
    what the server owes it is the count that makes the tab worth opening.
    """
    _seed(recovery_db, "send_failed")
    with TestClient(app) as client:
        payload = client.get("/api/ui/recovery").json()
    assert payload["pending"] >= 1
    screen = pathlib.Path("frontend/src/screens/Simple.tsx").read_text(encoding="utf-8")
    assert 'params.get("tab") ?? "recovery"' in screen


def test_both_tabs_are_served_and_neither_hides_the_other(recovery_db) -> None:
    """A failure arriving while you read logs must not be invisible: both tabs are on
    one screen, each backed by its own endpoint."""
    _seed(recovery_db, "send_failed")
    with TestClient(app) as client:
        assert client.get("/api/ui/logs").status_code == 200
        assert client.get("/api/ui/recovery").json()["pending"] >= 1
    screen = pathlib.Path("frontend/src/screens/Simple.tsx").read_text(encoding="utf-8")
    assert '"복구"' in screen and '"로그"' in screen


def test_operations_screen_is_reachable_without_a_session_user(recovery_db) -> None:
    """Basic/localhost mode has no users, so role=="admin" can never be true there.

    /logs demanded exactly that while the sidebar kept offering the link, so the page
    403'd for every local operator. Merging recovery in would have taken that with it.
    """
    with TestClient(app) as client:
        assert client.get("/logs").status_code == 200


def test_retry_rewrites_the_draft_and_does_not_send(recovery_db) -> None:
    """재시도는 **다시 쓰는 데까지**다 — 이 화면에서 고객에게 메일이 나가면 안 된다.

    예전에는 상태를 곧장 ``approved`` 로 바꿨고 발송 워커가 1분 안에 집어 같은 글을 그대로
    다시 보냈다. 실패가 배달 사고만은 아니라서(초안 자체가 틀렸을 수 있다) 같은 글을 다시
    보내는 것은 대개 답이 아니고, 무엇보다 여기는 무엇이 고장났는지 보는 자리다.
    발송은 티켓 세부 내역에서 글을 읽고 누른다 (2026-08-26 운영자 지시).
    """
    message_id, _job_id = _seed(recovery_db, "send_failed")
    with TestClient(app) as client:
        response = client.post(
            f"/operations/recovery/messages/{message_id}/retry",
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    with recovery_db() as session:
        message = session.get(Message, message_id)
        assert message.status == "drafting"
        assert message.send_error is None
        assert session.scalar(select(Event).where(Event.kind == "operator_recovery"))
        # 다시 쓰라는 작업이 큐에 실제로 올라가야 한다 — 안 그러면 drafting 인 채로 굳는다.
        queued = session.scalars(
            select(InboundJob).where(InboundJob.source == "console_redraft")
        ).all()
        assert len(queued) == 1
        assert queued[0].payload["draft_message_id"] == message_id


def test_unknown_delivery_requires_explicit_resolution(recovery_db) -> None:
    message_id, _job_id = _seed(recovery_db, "delivery_unknown")
    with TestClient(app) as client:
        response = client.post(
            f"/operations/recovery/messages/{message_id}/resolve",
            data={"action": "confirmed_sent"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    with recovery_db() as session:
        message = session.get(Message, message_id)
        assert message.status == "sent"
        assert message.sent_at is not None
        assert message.conversation.stage == "meeting_link_sent"


def test_dead_inbound_job_can_be_requeued(recovery_db) -> None:
    _message_id, job_id = _seed(recovery_db, "send_failed")
    with TestClient(app) as client:
        response = client.post(
            f"/operations/recovery/inbound/{job_id}/retry",
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    with recovery_db() as session:
        job = session.get(InboundJob, job_id)
        assert job.status == "pending"
        assert job.attempts == 0
        assert job.last_error is None


def test_a_failed_send_can_be_approved_again(recovery_db) -> None:
    """재발송이 곧 재승인이다 — 티켓 세부 내역의 「검토 완료 · 발송」이 실패한 초안에도 듣는다.

    발송 실패는 「고객에게 아무것도 안 갔다」는 뜻이라(400 이면 HubSpot 이 아무것도 만들지
    않는다) 다시 보내겠다는 판단은 처음 보내겠다는 판단과 같다. ``delivery_unknown`` 은
    일부러 빠져 있다 — 그건 「갔는지 모른다」라서 고객이 같은 메일을 두 번 받을 수 있고,
    그 판단은 복구 화면의 「발송됨 확인 / 미발송 확인」이 따로 받는다.
    """
    from src.agents.approval import ApprovalError, approve

    message_id, _ = _seed(recovery_db, "send_failed")
    with patch("src.agents.approval.SessionLocal", recovery_db):
        approve(message_id, approver="operator")
        with recovery_db() as session:
            assert session.get(Message, message_id).status == "approved"

        # 「갔는지 모른다」는 다른 이야기다 — 여기서 다시 승인되면 안 된다.
        with recovery_db() as session:
            session.get(Message, message_id).status = "delivery_unknown"
            session.commit()
        with pytest.raises(ApprovalError):
            approve(message_id, approver="operator")
