"""Tests for the unsubscribe web route."""

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
from src.db.models import EmailSuppression
from src.integrations.compliance import generate_unsub_token


@pytest.fixture()
def unsub_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    # The unsubscribe route persists via compliance.suppress_email, which uses
    # src.db.session.SessionLocal — that one patch covers it.
    with patch("src.db.session.SessionLocal", factory):
        yield factory


def _client() -> TestClient:
    return TestClient(app)


def test_unsubscribe_missing_params(unsub_db):
    r = _client().get("/unsubscribe")
    assert r.status_code == 400


def test_unsubscribe_invalid_token(unsub_db):
    r = _client().get("/unsubscribe?email=test@example.com&token=bad")
    assert r.status_code == 400
    assert "유효하지 않은" in r.text


def test_unsubscribe_valid(unsub_db):
    email = "victim@example.com"
    token = generate_unsub_token(email)
    r = _client().get(f"/unsubscribe?email={email}&token={token}")
    assert r.status_code == 200
    assert "수신 거부" in r.text
    session = unsub_db()
    sup = session.get(EmailSuppression, email)
    assert sup is not None
    assert sup.reason == "unsubscribe"
    session.close()
