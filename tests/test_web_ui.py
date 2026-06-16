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


def _client() -> TestClient:
    return TestClient(app)


def _mock_dashboard_context(flow="all"):
    return {
        "flow": flow,
        "recent_messages": [
            {
                "id": 1,
                "status": "sent",
                "category": "pricing_question",
                "subject": "가격 문의",
                "channel": "email",
                "direction": "outgoing",
                "flow": "inbound_reply",
                "created_at": datetime(2026, 1, 1, 12, 0),
            }
        ],
        "status_counts": {
            "pending_approval": 2,
            "approved": 1,
            "sent": 5,
            "bounced": 0,
            "replied": 1,
        },
        "today_sent": 3,
        "daily_limit": 100,
        "category_counts": [("pricing_question", 4), ("purchase_inquiry", 2)],
    }


def _mock_detail_context(message_id):
    if message_id == 1:
        return {
            "thread": [
                {
                    "id": 1,
                    "direction": "outbound",
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
    assert "대시보드" in r.text


@patch("src.api.web.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_loads_design_css():
    # The redesign replaced the Tailwind CDN with the PERSO design tokens/components CSS.
    r = _client().get("/")
    assert "/static/console.css" in r.text
    assert "/static/tokens.css" in r.text


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
def test_dashboard_shows_status_counts():
    # KPI cards surface the counts with Korean labels (status pills), not raw enum strings.
    r = _client().get("/")
    assert "승인 대기" in r.text
    assert "발송됨" in r.text


@patch("src.api.web.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_shows_recent_messages():
    r = _client().get("/")
    assert "가격 문의" in r.text


@patch("src.api.web.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_shows_category_counts():
    r = _client().get("/")
    assert "pricing_question" in r.text


@patch("src.api.web.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_shows_daily_send_stats():
    r = _client().get("/")
    assert "오늘 발송" in r.text
    assert "100" in r.text


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
    conv = Conversation(contact_id=contact.id, topic="test")
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
    r = _client().post(f"/messages/{pending_msg}/send", data={"body": "edited", "subject": ""})
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
    _client().post(f"/messages/{pending_msg}/send", data={"body": "", "subject": ""})
    r = _client().post(f"/messages/{pending_msg}/send", data={"body": "", "subject": ""})
    assert r.status_code == 400


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


def _mock_messages_list_context(status="", channel="", flow="all"):
    return {
        "messages": [
            {
                "id": 1,
                "status": "sent",
                "category": "pricing_question",
                "subject": "가격 문의",
                "channel": "email",
                "direction": "outgoing",
                "flow": "inbound_reply",
                "to_address": "buyer@example.com",
                "created_at": datetime(2026, 1, 1, 12, 0),
            }
        ],
        "filter_status": status,
        "filter_channel": channel,
        "filter_flow": flow,
    }


@patch("src.api.web.routes.messages._messages_list_context", _mock_messages_list_context)
def test_messages_list_returns_200():
    r = _client().get("/messages")
    assert r.status_code == 200
    assert "메시지" in r.text
    assert "가격 문의" in r.text


@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_message_edit_blocked_after_approve(mock_send, pending_msg):
    _client().post(f"/messages/{pending_msg}/send", data={"body": "", "subject": ""})
    r = _client().post(f"/messages/{pending_msg}/edit", data={"body": "x", "subject": ""})
    assert r.status_code == 400
