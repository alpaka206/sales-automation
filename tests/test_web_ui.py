"""Tests for web UI — dashboard, message detail, send/reject/edit actions."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db import models as _models  # noqa: F401
from src.db.models import Contact, Conversation, Message


@pytest.fixture(autouse=True)
def _worker_off(monkeypatch):
    """Pin the background worker OFF so the inline approve→send path is deterministic
    regardless of the local .env (a test that wants it on re-patches in its body)."""
    from src.common.config import settings

    monkeypatch.setattr(settings, "SEND_WORKER_ENABLED", False)


def _client() -> TestClient:
    return TestClient(app)


def _mock_dashboard_context():
    return {
        "recent_messages": [
            {
                "id": 1,
                "status": "pending_approval",
                "stage": "new",
                "subject": "가격 문의",
                "channel": "email",
                "created_at": datetime(2026, 1, 1, 12, 0),
            }
        ],
        "awaiting_total": 7,
        "awaiting_new": 4,
        "awaiting_negotiation": 3,
        "received_today": 4,
        # The board renders below the queue; an empty board is enough for these tests.
        "stages": [{"key": "new", "label": "New", "description": "새 문의", "rows": []}],
        "stage_options": (("new", "New", "새 문의"),),
        "stage_labels": {"new": "New"},
    }


def _mock_detail_context(message_id):
    if message_id == 1:
        return {
            "thread": [
                {
                    "id": 1,
                    "direction": "outgoing",
                    "status": "pending_approval",
                    "subject": "가격 안내",
                    "body": "안녕하세요, 가격 안내드립니다.",
                    "body_ko": None,
                    "channel": "email",
                    "from_address": "sales@company.com",
                    "to_address": "test@example.com",
                    "created_at": datetime(2026, 1, 1, 12, 0),
                    "sent_at": None,
                    "is_current": True,
                }
            ],
            "ticket": {"ticket_id": "T-1", "stage": "initial", "topic": "pricing_question"},
            "inbound_messages": [],
            "msg": {
                "id": 1,
                "status": "pending_approval",
                "subject": "가격 안내",
                "body": "안녕하세요, 가격 안내드립니다.",
                "body_ko": None,
                "channel": "email",
                "direction": "outgoing",
                "language": "ko",
                "to_address": "test@example.com",
                "from_address": "sales@company.com",
                "score_snapshot": 80,
                "scheduled_at": None,
                "sent_at": None,
                "created_at": datetime(2026, 1, 1, 12, 0),
                "category": "pricing_question",
            },
            "contact": {
                "id": 1,
                "name": "Test User",
                "email": "test@example.com",
                "company": "TestCo",
            },
            "prospect": None,
            "domain_profile": None,
        }
    return {}


@patch("src.api.web.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_returns_200():
    r = _client().get("/")
    assert r.status_code == 200
    assert "인바운드" in r.text


@patch("src.api.web.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_loads_design_css():
    r = _client().get("/")
    assert "/static/console.css" in r.text
    assert "/static/tokens.css" in r.text
    assert "theme-toggle" not in r.text
    assert "data-theme" not in r.text
    assert "새 문의에서 답변 발송까지" not in r.text


@patch("src.api.web.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_has_htmx():
    r = _client().get("/")
    assert "htmx.org" in r.text


@patch("src.api.web.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_loads_pretendard_tokens():
    # Pretendard (Korean UI font) is loaded via tokens.css, which the page links.
    r = _client().get("/")
    assert "/static/tokens.css" in r.text


@patch("src.api.web.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_shows_queue_counters():
    """The five KPI cards became four inline counters beside the queue heading."""
    r = _client().get("/")
    assert "답변 대기중인 문의" in r.text
    for label in ("오늘 접수", "ALL", "New", "Negotiating"):
        assert label in r.text, label


@patch("src.api.web.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_shows_recent_messages():
    r = _client().get("/")
    assert "가격 문의" in r.text


@patch("src.api.web.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_hosts_the_pipeline_board():
    """The board moved here from /pipeline, below the queue."""
    r = _client().get("/")
    assert "data-pipeline-board" in r.text
    assert "문의 파이프라인" in r.text


@patch("src.api.web.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_no_longer_shows_inquiry_type():
    """문의 유형 is retired: the panel, the column, and the stored value are all gone."""
    r = _client().get("/")
    assert "문의 유형" not in r.text


@patch("src.api.web.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_message_link():
    r = _client().get("/")
    assert "/messages/1" in r.text


# ---------- Message detail ----------


@patch("src.api.web.routes.messages._message_detail_context", _mock_detail_context)
def test_message_detail_returns_200():
    r = _client().get("/messages/1")
    assert r.status_code == 200
    assert "가격 안내" in r.text
    assert "pricing_question" in r.text
    assert "Test User" in r.text


@patch("src.api.web.routes.messages._message_detail_context", _mock_detail_context)
def test_message_detail_404_for_missing():
    r = _client().get("/messages/99999")
    assert r.status_code == 404


@patch("src.api.web.routes.messages._message_detail_context", _mock_detail_context)
def test_message_detail_shows_send_button():
    r = _client().get("/messages/1")
    assert "발송" in r.text  # "검토 완료 · 발송"
    assert "거절" in r.text


def test_message_detail_embeds_customer_history(_use_test_db):
    """The reply detail page surfaces the customer's CRM state, contract, and
    cross-channel touchpoints inline (via the real _message_detail_context /
    _customer_history), so the operator doesn't leave for the /customers page."""
    from datetime import datetime, timezone

    from src.db.models import ContractRecord, CustomerInteraction, CustomerProfile

    session = _use_test_db()
    contact = Contact(
        normalized_email="buyer@acme.com", full_name="Acme Buyer",
        email="buyer@acme.com", domain="acme.com", company="Acme",
    )
    session.add(contact)
    session.flush()
    contact_id = contact.id
    conv = Conversation(contact_id=contact_id, inquiry_subject="가격 문의")
    session.add(conv)
    session.flush()
    msg = Message(
        conversation_id=conv.id, direction="outgoing", channel="email",
        subject="안내", body="안녕하세요", status="pending_approval",
    )
    session.add(msg)
    session.add(CustomerProfile(
        contact_id=contact_id, customer_state="service", pipeline_stage="active",
        lead_temperature="hot", current_plan="PERSO Pro", next_action="금요일 재연락",
    ))
    session.add(CustomerInteraction(
        contact_id=contact_id, channel="meeting", direction="outgoing",
        subject="킥오프 미팅", summary="온보딩 일정 확정",
        happened_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    ))
    session.add(ContractRecord(contact_id=contact_id, status="active", plan="PERSO Pro", currency="KRW"))
    session.commit()
    msg_id = msg.id
    session.close()

    r = _client().get(f"/messages/{msg_id}")
    assert r.status_code == 200
    assert "고객 히스토리" in r.text          # the embedded panel
    assert "서비스 이용중" in r.text          # customer_state label
    assert "금요일 재연락" in r.text          # next action
    assert "킥오프 미팅" in r.text            # interaction touchpoint
    # The panel is scoped to THIS customer and no longer links out: the full history
    # lives in its own sidebar section (고객 히스토리 → 인바운드 고객 히스토리) now.
    assert f"/customers/{contact_id}" not in r.text


# ---------- Message actions (send/reject/edit) ----------


@pytest.fixture()
def _use_test_db():
    """Shared in-memory DB for route + approval integration tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with (
        patch("src.api.web.routes.messages.SessionLocal", factory),
        patch("src.agents.approval.SessionLocal", factory),
        patch("src.agents.send_worker.SessionLocal", factory),
        patch("src.integrations.senders.SessionLocal", factory, create=True),
    ):
        yield factory


@pytest.fixture()
def pending_msg(_use_test_db):
    """Insert a pending_approval message and return its id."""
    factory = _use_test_db
    session = factory()
    contact = Contact(normalized_email="t@e.com", full_name="T", email="t@e.com")
    session.add(contact)
    session.flush()
    conv = Conversation(contact_id=contact.id, inquiry_subject="test")
    session.add(conv)
    session.flush()
    msg = Message(
        conversation_id=conv.id,
        direction="outgoing",
        channel="email",
        subject="Test",
        body="Hello",
        status="pending_approval",
    )
    session.add(msg)
    session.commit()
    msg_id = msg.id
    session.close()
    return msg_id


@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_message_send_approves(mock_send, pending_msg, _use_test_db):
    r = _client().post(f"/messages/{pending_msg}/send", data={"body": "edited", "subject": "Test"})
    assert r.status_code == 200
    assert "승인" in r.text
    mock_send.assert_awaited_once()
    session = _use_test_db()
    m = session.get(Message, pending_msg)
    # Human approval dispatches immediately, so the message is sent, not queued.
    assert m.status == "sent"
    assert m.body == "edited"
    session.close()


@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_message_send_prevents_double(mock_send, pending_msg):
    _client().post(f"/messages/{pending_msg}/send", data={"body": "Hello", "subject": "Test"})
    r = _client().post(f"/messages/{pending_msg}/send", data={"body": "Hello", "subject": "Test"})
    assert r.status_code == 400


@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_message_send_defers_to_worker_when_enabled(mock_send, pending_msg, _use_test_db):
    """With the background worker on, /send approves but does NOT inline-send (the
    worker claims approved rows) — prevents a double-send race."""
    from src.common.config import settings

    with patch.object(settings, "SEND_WORKER_ENABLED", True):
        r = _client().post(f"/messages/{pending_msg}/send", data={"body": "Hello", "subject": "Test"})
    assert r.status_code == 200
    mock_send.assert_not_awaited()
    session = _use_test_db()
    m = session.get(Message, pending_msg)
    assert m.status == "approved"  # left for the worker
    session.close()


def test_message_reject(pending_msg, _use_test_db):
    r = _client().post(f"/messages/{pending_msg}/reject", data={"reason": "tone"})
    assert r.status_code == 200
    assert "거절" in r.text
    session = _use_test_db()
    m = session.get(Message, pending_msg)
    assert m.status == "rejected"
    session.close()


def test_message_edit_saves(pending_msg, _use_test_db):
    r = _client().post(
        f"/messages/{pending_msg}/edit", data={"body": "new body", "subject": "new subj"}
    )
    assert r.status_code == 200
    assert "저장" in r.text
    session = _use_test_db()
    m = session.get(Message, pending_msg)
    assert m.body == "new body"
    assert m.subject == "new subj"
    session.close()


def _mock_messages_list_context(status="awaiting", stage="", sort="oldest"):
    return {
        "messages": [
            {
                "id": 1,
                "status": "pending_approval",
                "stage": "new",
                "subject": "가격 문의",
                "channel": "email",
                "email": "buyer@example.com",
                "received_at": datetime(2026, 1, 1, 12, 0),
                "waiting_since": datetime(2026, 1, 1, 12, 0),
            }
        ],
        "filter_status": status,
        "filter_stage": stage,
        "filter_sort": sort,
        "stage_labels": {"new": "New", "negotiation": "Negotiating"},
        "now": datetime(2026, 1, 2, 12, 0),
    }


@patch("src.api.web.routes.messages._messages_list_context", _mock_messages_list_context)
def test_messages_list_returns_200():
    r = _client().get("/messages")
    assert r.status_code == 200
    assert "답변 검토" in r.text
    assert "가격 문의" in r.text


@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_message_edit_blocked_after_approve(mock_send, pending_msg):
    _client().post(f"/messages/{pending_msg}/send", data={"body": "Hello", "subject": "Test"})
    r = _client().post(f"/messages/{pending_msg}/edit", data={"body": "x", "subject": ""})
    assert r.status_code == 400
