"""Tests for ICP rules web UI and DB integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db import models as _models  # noqa: F401
from src.db.models import ICPRule


@pytest.fixture()
def icp_db():
    """Shared in-memory DB for ICP rules tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with patch("src.api.web.routes.SessionLocal", factory):
        yield factory


@pytest.fixture()
def seed_rule(icp_db):
    """Insert a sample ICP rule."""
    session = icp_db()
    rule = ICPRule(source="youtube", criteria_md="## 가점\n+15: SaaS", enabled=True)
    session.add(rule)
    session.commit()
    session.close()


def _client() -> TestClient:
    return TestClient(app)


def test_icp_rules_list(icp_db):
    r = _client().get("/icp-rules")
    assert r.status_code == 200
    assert "youtube" in r.text
    assert "google_search" in r.text


def test_icp_rules_list_shows_active(seed_rule):
    r = _client().get("/icp-rules")
    assert "활성" in r.text


def test_icp_rules_edit_form(icp_db):
    r = _client().get("/icp-rules/youtube/edit")
    assert r.status_code == 200
    assert "youtube" in r.text


def test_icp_rules_edit_with_existing(seed_rule):
    r = _client().get("/icp-rules/youtube/edit")
    assert r.status_code == 200
    assert "SaaS" in r.text


def test_icp_rules_save_create(icp_db):
    r = _client().post("/icp-rules/google_search", data={
        "criteria_md": "## 기준\n+20: 한국 도메인",
        "enabled": "on",
    })
    assert r.status_code == 200
    assert "저장 완료" in r.text
    session = icp_db()
    rule = session.query(ICPRule).filter_by(source="google_search").first()
    assert rule is not None
    assert "한국 도메인" in rule.criteria_md
    assert rule.enabled is True
    session.close()


def test_icp_rules_save_update(seed_rule, icp_db):
    r = _client().post("/icp-rules/youtube", data={
        "criteria_md": "## 수정됨\n+30: AI 기업",
        "enabled": "on",
    })
    assert r.status_code == 200
    session = icp_db()
    rule = session.query(ICPRule).filter_by(source="youtube").first()
    assert "AI 기업" in rule.criteria_md
    session.close()


def test_icp_rules_disable(seed_rule, icp_db):
    r = _client().post("/icp-rules/youtube", data={
        "criteria_md": "## 가점", "enabled": "false",
    })
    assert r.status_code == 200
    session = icp_db()
    rule = session.query(ICPRule).filter_by(source="youtube").first()
    assert rule.enabled is False
    session.close()


def test_outbound_agent_loads_criteria(icp_db):
    """_load_icp_criteria returns criteria_md for enabled source rules."""
    session = icp_db()
    session.add(ICPRule(source="test_src", criteria_md="custom criteria", enabled=True))
    session.commit()
    session.close()

    from src.agents.outbound.agent import OutboundAgent

    with patch("src.db.session.SessionLocal", icp_db):
        agent = OutboundAgent.__new__(OutboundAgent)
        result = agent._load_icp_criteria("test_src")
        assert result == "custom criteria"
        assert agent._load_icp_criteria("nonexistent") == ""
