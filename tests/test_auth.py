"""Tests for Google-OAuth web-UI auth: signed session, domain gate, allowlist."""

from __future__ import annotations

import time
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.web import auth
from src.common.config import settings
from src.db.base import Base
from src.db.models import User


def _mem_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_session_roundtrip_and_tamper():
    with patch.object(settings, "SESSION_SECRET", "unit-secret"):
        tok = auth.make_session("a@estsoft.com", "Kim", "admin")
        payload = auth._unsign(tok)
        assert payload and payload["email"] == "a@estsoft.com" and payload["role"] == "admin"
        # tampered signature is rejected
        assert auth._unsign(tok[:-3] + "zzz") is None
        # garbage is rejected, not raised
        assert auth._unsign("not-a-token") is None


def test_session_expiry():
    with patch.object(settings, "SESSION_SECRET", "unit-secret"):
        expired = auth._sign({"email": "a@estsoft.com", "name": "x", "role": "member", "exp": int(time.time()) - 1})
        assert auth._unsign(expired) is None


def test_session_secret_mismatch_rejected():
    with patch.object(settings, "SESSION_SECRET", "secret-one"):
        tok = auth.make_session("a@estsoft.com", "Kim", "member")
    with patch.object(settings, "SESSION_SECRET", "secret-two"):
        assert auth._unsign(tok) is None


def test_domain_gate():
    with patch.object(settings, "ALLOWED_EMAIL_DOMAIN", "estsoft.com"):
        assert auth._domain_ok("person@estsoft.com")
        assert auth._domain_ok("Person@ESTSOFT.com")
        assert not auth._domain_ok("person@gmail.com")
        assert not auth._domain_ok("person@notestsoft.com")
        assert not auth._domain_ok("estsoft.com@gmail.com")


def test_allowlist_admin_and_member_and_pending():
    factory = _mem_factory()
    with patch.object(auth, "SessionLocal", factory), \
         patch.object(settings, "WEB_UI_ADMIN_EMAILS", "boss@estsoft.com"), \
         patch.object(settings, "WEB_UI_ALLOWED_EMAILS", "member@estsoft.com"):
        admin, ok = auth._login_or_pending("boss@estsoft.com", "Boss", None)
        assert ok and admin["role"] == "admin"

        member, ok = auth._login_or_pending("member@estsoft.com", "Mem", None)
        assert ok and member["role"] == "member"

        newcomer, ok = auth._login_or_pending("new@estsoft.com", "New", None)
        assert not ok  # pending approval until an admin approves

        # An admin approval flips the gate.
        session = factory()
        u = session.get(User, "new@estsoft.com")
        u.approved = True
        session.commit()
        session.close()
        _, ok2 = auth._login_or_pending("new@estsoft.com", "New", None)
        assert ok2
