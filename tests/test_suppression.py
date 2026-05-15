"""Tests for email suppression and compliance footer."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db import models as _models  # noqa: F401
from src.db.models import EmailSuppression


@pytest.fixture()
def compliance_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with patch("src.db.session.SessionLocal", factory):
        yield factory


def test_generate_unsub_token():
    from src.integrations.compliance import generate_unsub_token
    token = generate_unsub_token("test@example.com")
    assert len(token) == 32
    assert generate_unsub_token("test@example.com") == token
    assert generate_unsub_token("other@example.com") != token


def test_verify_unsub_token():
    from src.integrations.compliance import generate_unsub_token, verify_unsub_token
    token = generate_unsub_token("test@example.com")
    assert verify_unsub_token("test@example.com", token) is True
    assert verify_unsub_token("test@example.com", "wrong") is False


def test_append_footer_ko():
    from src.integrations.compliance import append_footer
    result = append_footer("본문입니다.", "test@example.com", "ko")
    assert "수신 거부" in result
    assert "unsubscribe" in result
    assert "test@example.com" in result


def test_append_footer_en():
    from src.integrations.compliance import append_footer
    result = append_footer("Hello.", "test@example.com", "en")
    assert "Unsubscribe" in result


def test_suppress_and_check(compliance_db):
    from src.integrations.compliance import is_suppressed, suppress_email
    assert is_suppressed("new@example.com") is False
    suppress_email("new@example.com", "unsubscribe")
    assert is_suppressed("new@example.com") is True


def test_suppress_idempotent(compliance_db):
    from src.integrations.compliance import suppress_email
    suppress_email("dup@example.com")
    suppress_email("dup@example.com")
    session = compliance_db()
    count = session.query(EmailSuppression).filter_by(email="dup@example.com").count()
    assert count == 1
    session.close()
