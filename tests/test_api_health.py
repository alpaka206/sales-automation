"""Tests for FastAPI healthz and auth middleware."""

from __future__ import annotations

import base64
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app, validate_startup_settings
from src.api.security import is_web_ui_path, web_role_allows
from src.api.web.routes._shared import external_url
from src.common.config import settings


def _basic(user: str, pw: str) -> dict[str, str]:
    raw = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _remote_client() -> TestClient:
    return TestClient(app, base_url="https://console.example.com", client=("203.0.113.10", 50000))


def test_healthz_no_token(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "database": True}


def test_healthz_has_request_id(client: TestClient) -> None:
    r = client.get("/healthz")
    assert "X-Request-ID" in r.headers


def test_protected_route_rejects_no_token(client: TestClient) -> None:
    r = client.post("/run/report")
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid or missing token"


def test_protected_route_accepts_valid_token(client: TestClient) -> None:
    with patch("src.agents.report.ReportAgent") as mock_agent:
        mock_agent.return_value.generate.return_value = "report body"
        r = client.post(
            "/run/report",
            headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@patch("src.integrations.hubspot.HubSpotClient")
@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 1})
def test_webhook_inbound(mock_handle, mock_hs_cls, client: TestClient, monkeypatch) -> None:
    # Inbound is ticket-only. Default policy is fail-closed (require signature);
    # for this unsigned smoke test, turn enforcement off.
    monkeypatch.setattr(settings, "HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE", False)
    monkeypatch.setattr(settings, "HUBSPOT_WEBHOOK_SECRET", "")
    # Ticket → primary contact resolution (webhook needs a contact to reply to).
    mock_hs_cls.return_value.get_ticket_primary_contact_sync.return_value = "C123"
    r = client.post(
        "/webhook/hubspot/inbound",
        json={"subscriptionType": "ticket.creation", "objectId": 123, "occurredAt": 1684000000000},
        headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"
    assert r.json()["results"][0]["status"] in {"queued", "duplicate"}
    mock_handle.assert_not_called()


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


def test_web_ui_localhost_allowed() -> None:
    """A real loopback peer + localhost Host passes the local-development gate."""
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        r = client.get("/messages")
    assert r.status_code not in (401, 403)


def test_google_integration_routes_use_web_ui_auth_gate() -> None:
    assert is_web_ui_path("/integrations/google-sheets/connect") is True
    assert is_web_ui_path("/integrations/google-sheets/callback") is True


def test_operator_pages_use_web_ui_auth_gate() -> None:
    # /companies (domain history) and /contacts/{id}/edit must pass through the
    # browser auth/role/CSRF gate, not fall through to the internal-token API gate
    # (which would 401 a logged-in operator's browser).
    assert is_web_ui_path("/companies/example.com") is True
    assert is_web_ui_path("/contacts/1/edit") is True


def test_external_operator_links_allow_only_http_schemes() -> None:
    assert external_url("https://docs.example.com/a") == "https://docs.example.com/a"
    assert external_url("javascript:alert(1)") == ""
    assert external_url("//evil.example/path") == ""


def test_three_role_web_policy() -> None:
    assert web_role_allows("viewer", "GET", "/messages")
    assert not web_role_allows("viewer", "POST", "/messages/1/send")
    assert web_role_allows("member", "POST", "/messages/1/send")
    assert web_role_allows("operator", "POST", "/pipeline/1/stage")
    assert web_role_allows("operator", "POST", "/operations/recovery/messages/1/retry")
    assert not web_role_allows("operator", "POST", "/integrations/1")
    assert not web_role_allows("operator", "PUT", "/email-templates/1")
    assert not web_role_allows("operator", "POST", "/logs/clear")
    assert not web_role_allows("operator", "GET", "/integrations/google-sheets/connect")
    assert web_role_allows("admin", "DELETE", "/email-templates/1")


def test_viewer_mutation_is_blocked_by_middleware(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUTH_MODE", "google_oauth")
    with patch(
        "src.api.main.current_user",
        return_value={"email": "v@example.com", "name": "V", "role": "viewer"},
    ):
        response = TestClient(app).post("/messages/999/edit", data={"body": "x"})
    assert response.status_code == 403
    assert response.json()["detail"] == "insufficient role for this action"


def test_operator_admin_mutation_is_blocked_by_middleware(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUTH_MODE", "google_oauth")
    with patch(
        "src.api.main.current_user",
        return_value={"email": "o@example.com", "name": "O", "role": "operator"},
    ):
        response = TestClient(app).post("/integrations", data={"title": "x"})
    assert response.status_code == 403
    assert response.json()["detail"] == "insufficient role for this action"


def test_startup_rejects_multiple_in_process_workers(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WEB_CONCURRENCY", 2)
    monkeypatch.setattr(settings, "INBOUND_WORKER_ENABLED", True)
    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY"):
        validate_startup_settings()


def test_startup_rejects_public_basic_without_password(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WEB_CONCURRENCY", 1)
    monkeypatch.setattr(settings, "APP_HOST", "0.0.0.0")
    monkeypatch.setattr(settings, "AUTH_MODE", "basic")
    monkeypatch.setattr(settings, "WEB_UI_PASSWORD", "")
    with pytest.raises(RuntimeError, match="WEB_UI_PASSWORD"):
        validate_startup_settings()


def test_startup_rejects_incomplete_google_oauth(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WEB_CONCURRENCY", 1)
    monkeypatch.setattr(settings, "APP_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "AUTH_MODE", "google_oauth")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "")
    monkeypatch.setattr(settings, "SESSION_SECRET", "")
    with pytest.raises(RuntimeError, match="GOOGLE_OAUTH_CLIENT_SECRET"):
        validate_startup_settings()


def test_startup_rejects_sheets_oauth_without_dedicated_secrets(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WEB_CONCURRENCY", 1)
    monkeypatch.setattr(settings, "APP_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(settings, "AUTH_MODE", "basic")
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(settings, "SESSION_SECRET", "")
    monkeypatch.setattr(settings, "GOOGLE_TOKEN_ENCRYPTION_KEY", "")

    with pytest.raises(RuntimeError, match="GOOGLE_TOKEN_ENCRYPTION_KEY"):
        validate_startup_settings()


def test_web_ui_public_no_password_is_403(monkeypatch) -> None:
    """Non-localhost + no WEB_UI_PASSWORD → localhost-only gate (403)."""
    monkeypatch.setattr(settings, "WEB_UI_PASSWORD", "")
    r = _remote_client().get("/messages")
    assert r.status_code == 403
    assert r.json()["detail"] == "web UI is localhost-only"


def test_web_ui_public_requires_basic_auth(monkeypatch) -> None:
    """Non-localhost + WEB_UI_PASSWORD set, no creds → 401 with WWW-Authenticate."""
    monkeypatch.setattr(settings, "WEB_UI_PASSWORD", "s3cret")
    r = _remote_client().get("/messages")
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate", "").startswith("Basic")


def test_web_ui_public_wrong_password_is_401(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WEB_UI_PASSWORD", "s3cret")
    monkeypatch.setattr(settings, "WEB_UI_USERNAME", "admin")
    r = _remote_client().get("/messages", headers=_basic("admin", "wrong"))
    assert r.status_code == 401


def test_web_ui_public_correct_basic_auth_allowed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WEB_UI_PASSWORD", "s3cret")
    monkeypatch.setattr(settings, "WEB_UI_USERNAME", "admin")
    r = _remote_client().get("/messages", headers=_basic("admin", "s3cret"))
    assert r.status_code not in (401, 403)


def test_cross_site_web_write_is_blocked(client: TestClient) -> None:
    r = client.post(
        "/messages/999/edit",
        data={"body": "x"},
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "cross-site request blocked"


def test_security_headers_and_remote_docs_gate(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert _remote_client().get("/docs").status_code == 404


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
