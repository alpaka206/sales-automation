from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.web.routes.customer_ops import GOOGLE_SHEETS_STATE_COOKIE
from src.integrations import google_oauth


def _configure(monkeypatch):
    monkeypatch.setattr(google_oauth.settings, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(
        google_oauth.settings, "GOOGLE_TOKEN_ENCRYPTION_KEY", "test-token-key"
    )
    monkeypatch.setattr(google_oauth.settings, "INTERNAL_API_TOKEN", "")
    # Sheets shares the web-login client; there is no Sheets-specific one to set.
    monkeypatch.setattr(google_oauth.settings, "GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setattr(google_oauth.settings, "GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")


def test_oauth_state_is_signed_and_tamper_evident(monkeypatch):
    _configure(monkeypatch)
    state = google_oauth.make_state()
    google_oauth.validate_state(state)

    with pytest.raises(google_oauth.GoogleOAuthError):
        google_oauth.validate_state(state[:-1] + ("A" if state[-1] != "A" else "B"))


def test_authorization_url_requests_only_identity_and_sheets(monkeypatch):
    _configure(monkeypatch)
    url = google_oauth.authorization_url("http://localhost/callback", "signed-state")

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=client-id" in url
    assert "access_type=offline" in url
    assert "spreadsheets" in url
    assert "state=signed-state" in url


def test_grant_encryption_round_trip(monkeypatch):
    _configure(monkeypatch)
    payload = {"refresh_token": "secret-refresh", "access_token": "short-lived"}
    encrypted = google_oauth._encrypt(payload)

    assert "secret-refresh" not in encrypted
    assert google_oauth._decrypt(encrypted) == payload


def test_grant_encryption_does_not_reuse_session_secret(monkeypatch):
    _configure(monkeypatch)
    encrypted = google_oauth._encrypt({"refresh_token": "secret-refresh"})
    monkeypatch.setattr(google_oauth.settings, "GOOGLE_TOKEN_ENCRYPTION_KEY", "rotated-key")

    with pytest.raises(google_oauth.GoogleOAuthError):
        google_oauth._decrypt(encrypted)


def test_grant_encryption_requires_dedicated_key(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(google_oauth.settings, "GOOGLE_TOKEN_ENCRYPTION_KEY", "")

    with pytest.raises(google_oauth.GoogleOAuthError, match="GOOGLE_TOKEN_ENCRYPTION_KEY"):
        google_oauth._encrypt({"refresh_token": "secret-refresh"})


def test_env_refresh_token_is_a_complete_grant_without_the_browser(monkeypatch):
    """The whole point: no click, no database row, no encryption key needed.

    _build_service only ever uses refresh_token + the client id/secret, so this payload
    is sufficient. expires_at=0 is epoch — already expired — which is what makes
    google-auth fetch a fresh access token on the first call.
    """
    monkeypatch.setattr(google_oauth.settings, "GOOGLE_TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(google_oauth.settings, "SESSION_SECRET", "")
    monkeypatch.setattr(
        google_oauth.settings, "GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN", "1//env-refresh"
    )
    monkeypatch.setattr(
        google_oauth.settings, "GOOGLE_SHEETS_ACCOUNT_EMAIL", "owner@estsoft.com"
    )

    payload, email = google_oauth.load_grant()
    assert payload["refresh_token"] == "1//env-refresh"
    assert payload["expires_at"] == 0
    assert email == "owner@estsoft.com"


def test_env_grant_requests_only_the_sheets_scope(monkeypatch):
    """openid/email are browser-flow-only; asking for them warns on every refresh.

    The consent screen grants what its Data Access page lists — spreadsheets — so
    google-auth logged "Not all requested scopes were granted … missing scopes email"
    on each token refresh until the env payload stopped claiming them.
    """
    monkeypatch.setattr(
        google_oauth.settings, "GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN", "1//env-refresh"
    )

    payload, _email = google_oauth.env_grant()
    assert payload["scopes"] == ["https://www.googleapis.com/auth/spreadsheets"]
    assert "email" not in payload["scopes"]


def test_env_refresh_token_wins_over_a_stored_grant(monkeypatch):
    """Env is the deployment's explicit choice of account; a stale row must not shadow it."""
    _configure(monkeypatch)
    monkeypatch.setattr(
        google_oauth.settings, "GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN", "1//env-refresh"
    )
    called = False

    def _fail_if_read(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("the database must not be consulted when env is set")

    monkeypatch.setattr(google_oauth, "SessionLocal", _fail_if_read)

    assert google_oauth.load_grant()[0]["refresh_token"] == "1//env-refresh"
    assert called is False


def test_blank_env_refresh_token_falls_back_to_the_stored_grant(monkeypatch):
    """An empty env var must not read as "connected" — the browser flow still works."""
    _configure(monkeypatch)
    monkeypatch.setattr(google_oauth.settings, "GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN", "   ")

    assert google_oauth.env_grant() is None


def test_connect_binds_sheets_oauth_state_to_browser_cookie():
    with patch.object(google_oauth, "make_state", return_value="signed-state"), patch.object(
        google_oauth,
        "authorization_url",
        return_value="https://accounts.google.com/o/oauth2/v2/auth?state=signed-state",
    ), TestClient(app) as client:
        response = client.get("/integrations/google-sheets/connect", follow_redirects=False)

    assert response.status_code == 302
    assert GOOGLE_SHEETS_STATE_COOKIE in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


def test_callback_rejects_state_from_another_browser_session():
    exchange = AsyncMock()
    with patch.object(google_oauth, "exchange_code", exchange), TestClient(app) as client:
        client.cookies.set(GOOGLE_SHEETS_STATE_COOKIE, "expected-state")
        response = client.get(
            "/integrations/google-sheets/callback?state=attacker-state&code=code",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "google=error" in response.headers["location"]
    exchange.assert_not_awaited()
