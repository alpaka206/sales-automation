"""Tests for HubSpot webhook payload mapping and signature verification."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.common.config import settings


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"X-Internal-Token": settings.INTERNAL_API_TOKEN}


@pytest.fixture(autouse=True)
def _disable_webhook_signature():
    with patch.object(settings, "HUBSPOT_WEBHOOK_SECRET", ""), \
         patch.object(settings, "HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE", False):
        yield


@pytest.fixture(autouse=True)
def _mock_ticket_contact_lookup():
    """Ticket webhooks resolve the primary contact via association. Mock that lookup
    so it returns a contact id without hitting HubSpot."""
    with patch("src.integrations.hubspot.HubSpotClient") as mock_cls:
        mock_cls.return_value.get_ticket_primary_contact_sync.return_value = "C-contact"
        yield mock_cls


def _sign_request(secret: str, body: bytes, url: str, ts_ms: int | None = None) -> dict[str, str]:
    """Generate HubSpot v3 signature headers."""
    import base64
    ts_ms = ts_ms or int(time.time() * 1000)
    message = f"POST{url}{body.decode('utf-8')}{ts_ms}"
    sig = base64.b64encode(
        hmac_mod.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "X-HubSpot-Signature-v3": sig,
        "X-HubSpot-Request-Timestamp": str(ts_ms),
    }


# ---------- HubSpot native payload ----------


@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 1})
def test_ticket_creation_payload(mock_handle, client: TestClient) -> None:
    payload = [
        {
            "subscriptionType": "ticket.creation",
            "objectId": 901,
            "occurredAt": 1684000000000,
        }
    ]
    r = client.post(
        "/webhook/hubspot/inbound",
        json=payload,
        headers=_auth_headers(),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "accepted"
    assert len(data["results"]) == 1
    assert data["results"][0]["status"] == "processed"
    mock_handle.assert_called_once()
    call_arg = mock_handle.call_args[0][0]
    assert call_arg["event_type"] == "ticket_created"
    assert call_arg["ticket_id"] == "901"
    assert call_arg["object_id"] == "C-contact"


@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 3})
def test_multi_event_payload(mock_handle, client: TestClient) -> None:
    payload = [
        {"subscriptionType": "ticket.creation", "objectId": 903, "occurredAt": 1684000002000},
        {"subscriptionType": "ticket.creation", "objectId": 904, "occurredAt": 1684000003000},
        # Non-ticket subscription → ignored
        {"subscriptionType": "deal.creation", "objectId": 905, "occurredAt": 1684000004000},
    ]
    r = client.post(
        "/webhook/hubspot/inbound",
        json=payload,
        headers=_auth_headers(),
    )
    assert r.status_code == 200
    data = r.json()
    results = data["results"]
    assert len(results) == 3
    assert results[0]["status"] == "processed"
    assert results[1]["status"] == "processed"
    assert results[2]["status"] == "ignored"
    assert mock_handle.call_count == 2


@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 4})
def test_single_object_not_array(mock_handle, client: TestClient) -> None:
    payload = {
        "subscriptionType": "ticket.creation",
        "objectId": 999,
        "occurredAt": 1684000005000,
    }
    r = client.post(
        "/webhook/hubspot/inbound",
        json=payload,
        headers=_auth_headers(),
    )
    assert r.status_code == 200
    assert r.json()["results"][0]["status"] == "processed"


# ---------- Ignored event types ----------


def test_ignored_subscription_type(client: TestClient) -> None:
    payload = [{"subscriptionType": "deal.creation", "objectId": 800, "occurredAt": 1684000010000}]
    r = client.post(
        "/webhook/hubspot/inbound",
        json=payload,
        headers=_auth_headers(),
    )
    assert r.status_code == 200
    assert r.json()["results"][0]["status"] == "ignored"


# ---------- Error handling ----------


@patch("src.agents.inbound.InboundAgent.handle", side_effect=Exception("boom"))
def test_one_event_error_does_not_block_others(mock_handle, client: TestClient) -> None:
    payload = [
        {"subscriptionType": "ticket.creation", "objectId": 701, "occurredAt": 1684000020000},
        {"subscriptionType": "ticket.creation", "objectId": 702, "occurredAt": 1684000021000},
    ]
    # First call raises, second succeeds
    mock_handle.side_effect = [Exception("boom"), {"message_id": 7}]
    r = client.post(
        "/webhook/hubspot/inbound",
        json=payload,
        headers=_auth_headers(),
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["status"] == "error"
    assert results[1]["status"] == "processed"


def test_invalid_json_returns_400(client: TestClient) -> None:
    r = client.post(
        "/webhook/hubspot/inbound",
        content=b"not-json",
        headers={**_auth_headers(), "Content-Type": "application/json"},
    )
    assert r.status_code == 400


# ---------- Signature verification ----------


@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 8})
def test_valid_signature_accepted(mock_handle, client: TestClient) -> None:
    secret = "test-webhook-secret-123"
    payload = [{"subscriptionType": "ticket.creation", "objectId": 600, "occurredAt": 1684000030000}]
    body = json.dumps(payload).encode()
    url = "https://testserver/webhook/hubspot/inbound"
    sig_headers = _sign_request(secret, body, url)

    with patch.object(settings, "HUBSPOT_WEBHOOK_SECRET", secret), \
         patch.object(settings, "HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE", True):
        r = client.post(
            "/webhook/hubspot/inbound",
            content=body,
            headers={"Content-Type": "application/json", **sig_headers},
        )
    assert r.status_code == 200


def test_invalid_signature_rejected(client: TestClient) -> None:
    secret = "test-webhook-secret-456"
    payload = [{"subscriptionType": "ticket.creation", "objectId": 601}]
    body = json.dumps(payload).encode()
    ts = str(int(time.time() * 1000))

    with patch.object(settings, "HUBSPOT_WEBHOOK_SECRET", secret), \
         patch.object(settings, "HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE", True):
        r = client.post(
            "/webhook/hubspot/inbound",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-HubSpot-Signature-v3": "bad-signature",
                "X-HubSpot-Request-Timestamp": ts,
            },
        )
    assert r.status_code == 401


def test_missing_signature_headers_rejected(client: TestClient) -> None:
    secret = "test-webhook-secret-789"
    payload = [{"subscriptionType": "ticket.creation", "objectId": 602}]

    with patch.object(settings, "HUBSPOT_WEBHOOK_SECRET", secret), \
         patch.object(settings, "HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE", True):
        r = client.post(
            "/webhook/hubspot/inbound",
            json=payload,
            headers={},
        )
    assert r.status_code == 401


def test_expired_timestamp_rejected(client: TestClient) -> None:
    secret = "test-webhook-secret-exp"
    payload = [{"subscriptionType": "ticket.creation", "objectId": 603}]
    body = json.dumps(payload).encode()
    url = "https://testserver/webhook/hubspot/inbound"
    old_ts = int(time.time() * 1000) - 400_000  # 400 seconds ago > 300s max
    sig_headers = _sign_request(secret, body, url, ts_ms=old_ts)

    with patch.object(settings, "HUBSPOT_WEBHOOK_SECRET", secret), \
         patch.object(settings, "HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE", True):
        r = client.post(
            "/webhook/hubspot/inbound",
            content=body,
            headers={"Content-Type": "application/json", **sig_headers},
        )
    assert r.status_code == 401


@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 9})
def test_no_secret_configured_skips_verification(mock_handle, client: TestClient) -> None:
    """When HUBSPOT_WEBHOOK_SECRET is empty, signature verification is skipped (uses token auth)."""
    r = client.post(
        "/webhook/hubspot/inbound",
        json=[{"subscriptionType": "ticket.creation", "objectId": 604}],
        headers=_auth_headers(),
    )
    assert r.status_code == 200
