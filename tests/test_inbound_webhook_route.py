"""Tests for HubSpot webhook payload mapping and signature verification."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.common.config import settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"X-Internal-Token": settings.INTERNAL_API_TOKEN}


def _load_fixture(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _disable_webhook_signature():
    with patch.object(settings, "HUBSPOT_WEBHOOK_SECRET", ""), \
         patch.object(settings, "HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE", False):
        yield


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
def test_contact_creation_payload(mock_handle, client: TestClient) -> None:
    payload = _load_fixture("hubspot_webhook_contact_creation.json")
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
    assert call_arg["event_type"] == "contact.creation"
    assert call_arg["object_id"] == "901"


@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 2})
def test_lifecycle_change_payload(mock_handle, client: TestClient) -> None:
    payload = _load_fixture("hubspot_webhook_property_change.json")
    r = client.post(
        "/webhook/hubspot/inbound",
        json=payload,
        headers=_auth_headers(),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["results"][0]["status"] == "processed"
    call_arg = mock_handle.call_args[0][0]
    assert call_arg["event_type"] == "lifecycle_change"
    assert call_arg["object_id"] == "902"


@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 3})
def test_multi_event_payload(mock_handle, client: TestClient) -> None:
    payload = _load_fixture("hubspot_webhook_multi.json")
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
    # Third event: propertyChange for 'email' (not lifecyclestage) → ignored
    assert results[2]["status"] == "ignored"
    assert mock_handle.call_count == 2


@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 4})
def test_single_object_not_array(mock_handle, client: TestClient) -> None:
    payload = {
        "subscriptionType": "contact.creation",
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


# ---------- Legacy internal format ----------


@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 5})
def test_legacy_internal_format(mock_handle, client: TestClient) -> None:
    r = client.post(
        "/webhook/hubspot/inbound",
        json={"event_type": "contact.creation", "object_id": "123"},
        headers=_auth_headers(),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"
    mock_handle.assert_called_once()


@patch("src.agents.inbound.InboundAgent.handle", return_value={"message_id": 6})
def test_legacy_format_in_array(mock_handle, client: TestClient) -> None:
    r = client.post(
        "/webhook/hubspot/inbound",
        json=[{"event_type": "contact.creation", "object_id": "456"}],
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


def test_property_change_non_lifecycle_ignored(client: TestClient) -> None:
    payload = [
        {
            "subscriptionType": "contact.propertyChange",
            "objectId": 810,
            "occurredAt": 1684000011000,
            "propertyName": "firstname",
            "propertyValue": "Alice",
        }
    ]
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
        {"subscriptionType": "contact.creation", "objectId": 701, "occurredAt": 1684000020000},
        {"subscriptionType": "contact.creation", "objectId": 702, "occurredAt": 1684000021000},
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
    payload = [{"subscriptionType": "contact.creation", "objectId": 600, "occurredAt": 1684000030000}]
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
    payload = [{"subscriptionType": "contact.creation", "objectId": 601}]
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
    payload = [{"subscriptionType": "contact.creation", "objectId": 602}]

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
    payload = [{"subscriptionType": "contact.creation", "objectId": 603}]
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
        json=[{"subscriptionType": "contact.creation", "objectId": 604}],
        headers=_auth_headers(),
    )
    assert r.status_code == 200
