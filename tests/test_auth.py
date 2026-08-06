"""Tests for Google-OAuth web-UI auth: signed session, domain gate, allowlist."""

from __future__ import annotations

import time
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from src.api import auth
from src.api.main import app
from src.api.routes import settings_page
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


def test_first_login_bootstraps_admin_others_pending_until_approved():
    """Operators live in the DB only: first sign-in bootstraps, the rest wait."""
    factory = _mem_factory()
    with patch.object(auth, "SessionLocal", factory):
        # The very first account on an empty table becomes an approved admin.
        admin, ok = auth._login_or_pending("boss@estsoft.com", "Boss", None)
        assert ok and admin["role"] == "admin"

        # Once a row exists the bootstrap is dead: newcomers land unapproved.
        # `approved` is the gate, so the role they carry does not let them in.
        _newcomer, ok = auth._login_or_pending("new@estsoft.com", "New", None)
        assert not ok

        # An admin approval flips the gate.
        session = factory()
        u = session.get(User, "new@estsoft.com")
        u.approved = True
        session.commit()
        session.close()
        _, ok2 = auth._login_or_pending("new@estsoft.com", "New", None)
        assert ok2

        # Re-login never changes an existing row's approval.
        again, ok3 = auth._login_or_pending("new@estsoft.com", "New", None)
        assert ok3


def test_no_env_admin_allowlist_exists():
    """The env allowlist is gone — authorization must come from the users table."""
    assert not hasattr(settings, "WEB_UI_ADMIN_EMAILS")
    assert not hasattr(auth, "_admin_emails")


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
        # Legacy "member" is full access under the two-role model.
        assert auth.current_user(request)["role"] == "admin"

        # Demotion in the DB takes effect on the very next request.
        with factory() as session:
            user = session.get(User, "a@estsoft.com")
            user.role = "viewer"
            session.commit()
        assert auth.current_user(request)["role"] == "viewer"

        with factory() as session:
            user = session.get(User, "a@estsoft.com")
            user.approved = False
            session.commit()
        assert auth.current_user(request) is None


def test_only_two_roles_exist():
    """관리자 was merged into 운영자: everything that is not viewer is full access."""
    assert auth.normalize_role("viewer") == "viewer"
    for legacy in ("admin", "operator", "member", "", None, "something-else"):
        assert auth.normalize_role(legacy) == "admin", legacy


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

        # Promoting back lands on "admin" — the single full-access role.
        response = client.post(
            "/settings/users/reader@estsoft.com",
            data={"action": "make_admin"},
        )
        assert response.status_code == 204
        with factory() as session:
            assert session.get(User, "reader@estsoft.com").role == "admin"


def test_sign_in_renders_the_console_bundle_not_a_template():
    """The last screen to leave Jinja. It renders before there is a session, so it keeps
    the /auth/login URL — the one prefix the auth middleware lets through — and serves the
    same SPA document every other screen does."""
    from fastapi.testclient import TestClient

    from src.api.main import app

    with TestClient(app) as client:
        page = client.get("/auth/login")
        assert page.status_code == 200
        assert "/static/app/" in page.text          # the bundle, not a rendered page

        state = client.get("/auth/state")
        assert state.status_code == 200
        assert set(state.json()) == {"domain", "configured", "email"}


def test_a_refused_sign_in_says_why_without_putting_the_account_in_the_url():
    """The reason travels in the query string because the document is static. The
    attempted address must not: it is the one piece of personal data in the exchange."""
    import inspect

    from src.api.auth import _deny, auth_callback

    response = _deny("도메인이 다릅니다")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth/login?error=")
    # Query strings reach proxy and access logs, so no _deny message may carry the email.
    source = inspect.getsource(auth_callback)
    assert "_deny" in source and "{email" not in source


def test_the_access_screen_returns_a_list_not_a_500():
    """이 화면은 한동안 500 이었고, 프런트가 모든 실패를 "관리자만 접근할 수 있습니다" 로
    그려서 관리자에게 권한이 없다고 말했습니다. 원인은 사라진 함수를 부르는 import 였습니다.

    그래서 여기서 확인하는 것은 권한이 아니라 **200 과 화면이 읽는 모양**입니다.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from src.api.main import app
    from src.db.base import Base
    from src.db.models import User

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([
            User(email="admin@estsoft.com", name="관리자", role="admin", approved=True),
            # legacy 'member': normalize_role 이 admin 으로 풉니다. 화면은 실제로 적용되는
            # 권한을 보여야 합니다 — 'member' 라고 적으면 조회 전용처럼 읽힙니다.
            User(email="legacy@estsoft.com", name="옛 계정", role="member", approved=True),
            User(email="pending@estsoft.com", name="대기", role="admin", approved=False),
        ])
        session.commit()

    # 라우트가 호출 시점에 ...db.session 에서 가져오므로 거기를 갈아 끼웁니다.
    with patch("src.db.session.SessionLocal", factory):
        with TestClient(app) as client:
            response = client.get("/api/ui/settings/users")

    assert response.status_code == 200, response.text
    body = response.json()
    assert {"approved_users", "me_email", "domain"} <= set(body)
    emails = {u["email"]: u["role"] for u in body["approved_users"]}
    assert emails == {"admin@estsoft.com": "admin", "legacy@estsoft.com": "admin"}
