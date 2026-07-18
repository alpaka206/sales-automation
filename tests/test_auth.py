"""Tests for Google-OAuth web-UI auth: signed session, domain gate, allowlist."""

from __future__ import annotations

import time
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from src.api.web import auth
from src.api.main import app
from src.api.web.routes import settings_page
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


def _request_with_session(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"cookie", f"{auth.SESSION_COOKIE}={token}".encode())],
            "scheme": "https",
            "server": ("console.example.com", 443),
            "client": ("203.0.113.10", 50000),
        }
    )


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
        assert ok and member["role"] == "operator"

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


def test_current_user_uses_live_approval_and_role():
    factory = _mem_factory()
    with patch.object(auth, "SessionLocal", factory), patch.object(
        settings, "SESSION_SECRET", "unit-secret"
    ):
        with factory() as session:
            session.add(User(email="a@estsoft.com", name="A", approved=True, role="member"))
            session.commit()
        token = auth.make_session("a@estsoft.com", "Old name", "admin")
        request = _request_with_session(token)
        assert auth.current_user(request)["role"] == "operator"

        with factory() as session:
            user = session.get(User, "a@estsoft.com")
            user.role = "admin"
            session.commit()
        assert auth.current_user(request)["role"] == "admin"

        with factory() as session:
            user = session.get(User, "a@estsoft.com")
            user.approved = False
            session.commit()
        assert auth.current_user(request) is None


def test_role_normalization_keeps_legacy_members_operational():
    assert auth.normalize_role("member") == "operator"
    assert auth.normalize_role("operator") == "operator"
    assert auth.normalize_role("viewer") == "viewer"
    assert auth.normalize_role("admin") == "admin"


def test_admin_can_assign_viewer_and_operator_roles():
    factory = _mem_factory()
    admin = {"email": "boss@estsoft.com", "name": "Boss", "role": "admin"}
    with (
        patch.object(settings_page, "SessionLocal", factory),
        patch("src.api.main.current_user", return_value=admin),
        patch.object(settings, "AUTH_MODE", "google_oauth"),
        patch.object(settings, "GOOGLE_OAUTH_CLIENT_ID", "client-id"),
        patch.object(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "client-secret"),
        patch.object(settings, "SESSION_SECRET", "session-secret"),
        TestClient(app) as client,
    ):
        response = client.post(
            "/settings/users/add",
            data={"username": "reader", "role": "viewer"},
        )
        assert response.status_code == 204
        with factory() as session:
            assert session.get(User, "reader@estsoft.com").role == "viewer"

        response = client.post(
            "/settings/users/reader@estsoft.com",
            data={"action": "make_operator"},
        )
        assert response.status_code == 204
        with factory() as session:
            assert session.get(User, "reader@estsoft.com").role == "operator"
