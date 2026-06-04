"""Tests for FastAPI healthz and auth middleware."""

from __future__ import annotations

import base64
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.common.config import settings


def _basic(user: str, pw: str) -> dict[str, str]:
    raw = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_healthz_no_token(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_healthz_has_request_id(client: TestClient) -> None:
    r = client.get("/healthz")
    assert "X-Request-ID" in r.headers


def test_protected_route_rejects_no_token(client: TestClient) -> None:
    r = client.post("/run/reply_check")
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid or missing token"


def test_protected_route_accepts_valid_token(client: TestClient) -> None:
    r = client.post(
        "/run/reply_check",
        headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 1})
def test_webhook_inbound(mock_handle, client: TestClient, monkeypatch) -> None:
    # Default policy is fail-closed (require signature). For this legacy unsigned
    # payload test, explicitly turn enforcement off.
    monkeypatch.setattr(settings, "HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE", False)
    monkeypatch.setattr(settings, "HUBSPOT_WEBHOOK_SECRET", "")
    r = client.post(
        "/webhook/hubspot/inbound",
        json={"event_type": "contact.creation", "object_id": "123"},
        headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"
    mock_handle.assert_called_once()


def test_webhook_rejects_unsigned_when_required(client: TestClient, monkeypatch) -> None:
    """When HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE=True (default) and no secret is set,
    unsigned webhook calls must be refused (fail-closed)."""
    monkeypatch.setattr(settings, "HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE", True)
    monkeypatch.setattr(settings, "HUBSPOT_WEBHOOK_SECRET", "")
    r = client.post(
        "/webhook/hubspot/inbound",
        json={"event_type": "contact.creation", "object_id": "123"},
    )
    assert r.status_code == 503
    assert "HUBSPOT_WEBHOOK_SECRET" in r.json()["detail"]


def test_run_outbound_endpoint_imports_agent(client: TestClient) -> None:
    """Regression: POST /run/outbound must be able to import OutboundAgent.

    The `outbound/` package previously shadowed a dead `outbound.py`, so
    `from ..agents.outbound import OutboundAgent` raised ImportError at runtime.
    """
    with patch("src.agents.outbound.OutboundAgent") as mock_agent:
        mock_agent.return_value.run.return_value = {}
        r = client.post(
            "/run/outbound",
            json={"source": "manual_csv", "filters": {}},
            headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "started"
    mock_agent.return_value.run.assert_called_once()


def test_web_ui_localhost_allowed(client: TestClient, monkeypatch) -> None:
    """With APP_HOST bound to loopback, the web UI is treated as local → gate passes."""
    monkeypatch.setattr(settings, "APP_HOST", "127.0.0.1")
    r = client.get("/messages")
    assert r.status_code not in (401, 403)


def test_web_ui_public_no_password_is_403(client: TestClient, monkeypatch) -> None:
    """Non-localhost + no WEB_UI_PASSWORD → localhost-only gate (403)."""
    monkeypatch.setattr(settings, "APP_HOST", "0.0.0.0")
    monkeypatch.setattr(settings, "WEB_UI_PASSWORD", "")
    r = client.get("/messages")
    assert r.status_code == 403
    assert r.json()["detail"] == "web UI is localhost-only"


def test_web_ui_public_requires_basic_auth(client: TestClient, monkeypatch) -> None:
    """Non-localhost + WEB_UI_PASSWORD set, no creds → 401 with WWW-Authenticate."""
    monkeypatch.setattr(settings, "APP_HOST", "0.0.0.0")
    monkeypatch.setattr(settings, "WEB_UI_PASSWORD", "s3cret")
    r = client.get("/messages")
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate", "").startswith("Basic")


def test_web_ui_public_wrong_password_is_401(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "APP_HOST", "0.0.0.0")
    monkeypatch.setattr(settings, "WEB_UI_PASSWORD", "s3cret")
    monkeypatch.setattr(settings, "WEB_UI_USERNAME", "admin")
    r = client.get("/messages", headers=_basic("admin", "wrong"))
    assert r.status_code == 401


def test_web_ui_public_correct_basic_auth_allowed(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "APP_HOST", "0.0.0.0")
    monkeypatch.setattr(settings, "WEB_UI_PASSWORD", "s3cret")
    monkeypatch.setattr(settings, "WEB_UI_USERNAME", "admin")
    r = client.get("/messages", headers=_basic("admin", "s3cret"))
    assert r.status_code not in (401, 403)


def test_unsubscribe_public_without_auth(client: TestClient, monkeypatch) -> None:
    """/unsubscribe stays public (own signed token) even on a non-localhost deploy."""
    monkeypatch.setattr(settings, "APP_HOST", "0.0.0.0")
    monkeypatch.setattr(settings, "WEB_UI_PASSWORD", "s3cret")
    # No Basic Auth header, no internal token — must NOT be 401/403 from the gate.
    r = client.get("/unsubscribe", params={"email": "a@b.com", "token": "bad"})
    assert r.status_code not in (401, 403)


def test_approve_nonexistent_message_returns_400(client: TestClient, monkeypatch) -> None:
    """When APPROVAL_REQUIRE_TOKEN is on (default), the API requires a per-message
    HMAC token in the body. Provide a valid one to reach the not-found path."""
    monkeypatch.setattr(settings, "APPROVAL_REQUIRE_TOKEN", False)
    r = client.post(
        "/approve/42",
        json={"approver": "slack:U001", "action": "approve"},
        headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
    )
    assert r.status_code == 400
    assert "not found" in r.json()["detail"].lower()


def test_approve_rejects_missing_token(client: TestClient, monkeypatch) -> None:
    """When APPROVAL_REQUIRE_TOKEN is on, a missing token gives 403 (IDOR guard)."""
    monkeypatch.setattr(settings, "APPROVAL_REQUIRE_TOKEN", True)
    r = client.post(
        "/approve/42",
        json={"approver": "slack:U001", "action": "approve"},
        headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
    )
    assert r.status_code == 403


def test_approve_accepts_valid_token(client: TestClient, monkeypatch) -> None:
    """A correct per-message HMAC token bypasses the IDOR guard."""
    from src.agents.approval import make_approval_token

    monkeypatch.setattr(settings, "APPROVAL_REQUIRE_TOKEN", True)
    token = make_approval_token(42)
    r = client.post(
        "/approve/42",
        json={"approver": "slack:U001", "action": "approve", "token": token},
        headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
    )
    # Reaches the approve() logic — 400 because msg 42 doesn't exist, not 403.
    assert r.status_code == 400
    assert "not found" in r.json()["detail"].lower()
