"""Shared test fixtures."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.base import Base
from src.db import models as _models  # noqa: F401 — register all models with Base


@pytest.fixture()
def db_engine():
    """In-memory SQLite engine with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_session_factory(db_engine):
    """Sessionmaker bound to the in-memory engine."""
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture()
def db_session(db_session_factory) -> Session:
    """A single DB session, closed after the test."""
    session = db_session_factory()
    yield session
    session.close()


@pytest.fixture()
def mock_llm():
    """MagicMock LLMClient returning 'ok' by default."""
    llm = MagicMock()
    llm.complete.return_value = "ok"
    return llm


@pytest.fixture(autouse=True)
def _reset_api_quotas(monkeypatch):
    """Prevent quota tracking from polluting test runs."""
    monkeypatch.setattr("src.integrations.google_search._check_quota", lambda cost: None)
    monkeypatch.setattr("src.integrations.google_search._track_quota", lambda cost: None)
    monkeypatch.setattr("src.integrations.youtube._check_quota", lambda cost: None)
    monkeypatch.setattr("src.integrations.youtube._track_quota", lambda cost: None)
