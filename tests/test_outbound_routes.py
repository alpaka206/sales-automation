"""Tests for outbound intake and prospects web routes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db import models as _models  # noqa: F401
from src.db.models import Contact, Conversation, Message, Prospect


@pytest.fixture()
def ob_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with patch("src.api.web.routes.SessionLocal", factory), \
         patch("src.agents.approval.SessionLocal", factory):
        yield factory


def _client() -> TestClient:
    return TestClient(app)


def test_outbound_new_form(ob_db):
    r = _client().get("/outbound/new")
    assert r.status_code == 200
    assert "아웃바운드 발굴" in r.text


def test_run_intent_empty_query(ob_db):
    r = _client().post("/outbound/run-intent", data={"query": ""})
    assert r.status_code == 400


def test_run_intent_dispatched(ob_db):
    mock_dispatch = MagicMock(return_value={
        "status": "dispatched",
        "source": "youtube",
        "stats": {"discovered": 5},
    })
    with patch("src.agents.outbound.dispatcher.dispatch_natural_query", mock_dispatch), \
         patch("src.llm.client.LLMClient"):
        r = _client().post("/outbound/run-intent", data={"query": "SaaS 스타트업"})
    assert r.status_code == 200
    assert "발굴 완료" in r.text


def test_run_intent_rejected(ob_db):
    mock_dispatch = MagicMock(return_value={
        "status": "rejected",
        "confidence": 0.3,
        "rationale": "불명확한 요청",
    })
    with patch("src.agents.outbound.dispatcher.dispatch_natural_query", mock_dispatch), \
         patch("src.llm.client.LLMClient"):
        r = _client().post("/outbound/run-intent", data={"query": "뭔가"})
    assert r.status_code == 200
    assert "신뢰도 부족" in r.text


def test_prospects_list_empty(ob_db):
    r = _client().get("/prospects")
    assert r.status_code == 200
    assert "프로스펙트가 없습니다" in r.text


def test_prospects_list_with_data(ob_db):
    session = ob_db()
    session.add(Prospect(
        source="youtube", full_name="김철수", email="cs@test.com",
        normalized_email="cs@test.com", company="TestCo", status="analyzed",
        icp_score=75,
    ))
    session.commit()
    session.close()
    r = _client().get("/prospects")
    assert r.status_code == 200
    assert "김철수" in r.text


def test_prospects_filter_by_source(ob_db):
    session = ob_db()
    session.add(Prospect(source="youtube", full_name="A", normalized_email="a@t.com", status="analyzed"))
    session.add(Prospect(source="google_search", full_name="B", normalized_email="b@t.com", status="analyzed"))
    session.commit()
    session.close()
    r = _client().get("/prospects?source=youtube")
    assert "A" in r.text
    assert "B" not in r.text


def test_bulk_approve_empty(ob_db):
    r = _client().post("/prospects/bulk-approve")
    assert r.status_code == 400


def test_bulk_approve(ob_db):
    session = ob_db()
    contact = Contact(normalized_email="x@t.com", full_name="X", email="x@t.com")
    session.add(contact)
    session.flush()
    prospect = Prospect(
        source="youtube", full_name="X", normalized_email="x@t.com",
        contact_id=contact.id, status="analyzed",
    )
    session.add(prospect)
    session.flush()
    conv = Conversation(contact_id=contact.id, prospect_id=prospect.id)
    session.add(conv)
    session.flush()
    msg = Message(
        conversation_id=conv.id, direction="outgoing", channel="email",
        body="Hello", status="pending_approval",
    )
    session.add(msg)
    session.commit()
    pid = prospect.id
    session.close()

    r = _client().post("/prospects/bulk-approve", data={"prospect_id": str(pid)})
    assert r.status_code == 200
    assert "1건 승인" in r.text
