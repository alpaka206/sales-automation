"""Tests for FastAPI healthz and auth middleware."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.common.config import settings


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
    assert r.json()["status"] == "started"


@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 1})
def test_webhook_inbound(mock_handle, client: TestClient) -> None:
    r = client.post(
        "/webhook/hubspot/inbound",
        json={"event_type": "contact.creation", "object_id": "123"},
        headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"


def test_approve_message(client: TestClient) -> None:
    r = client.post(
        "/approve/42",
        json={"approver": "slack:U001", "action": "approve"},
        headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
    )
    assert r.status_code == 200
    assert r.json()["action"] == "approve"
    assert r.json()["message_id"] == 42
