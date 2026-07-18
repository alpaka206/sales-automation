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
from src.api.webhook import MAX_WEBHOOK_BODY_BYTES, MAX_WEBHOOK_EVENTS
from src.common.config import settings
from src.db.models import InboundJob
from src.db.session import SessionLocal


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
def _clean_inbound_jobs():
    with SessionLocal() as session:
        session.query(InboundJob).delete()
        session.commit()
    yield
    with SessionLocal() as session:
        session.query(InboundJob).delete()
        session.commit()


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
    assert data["results"][0]["status"] == "queued"
    mock_handle.assert_not_called()
    with SessionLocal() as session:
        job = session.query(InboundJob).one()
        assert job.event_key == "hubspot:ticket:901:created"


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
    assert results[0]["status"] == "queued"
    assert results[1]["status"] == "queued"
    assert results[2]["status"] == "ignored"
    mock_handle.assert_not_called()


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
    assert r.json()["results"][0]["status"] == "queued"
    mock_handle.assert_not_called()


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


def test_ticket_stage_change_to_new_is_queued_once(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "HUBSPOT_TICKET_STAGE_NEW", "new-stage")
    payload = [
        {
            "subscriptionType": "ticket.propertyChange",
            "objectId": 811,
            "propertyName": "hs_pipeline_stage",
            "propertyValue": "new-stage",
            "occurredAt": 1684000011000,
            "eventId": 44,
        },
        {
            "subscriptionType": "ticket.propertyChange",
            "objectId": 811,
            "propertyName": "hs_pipeline_stage",
            "propertyValue": "new-stage",
            "occurredAt": 1684000011000,
            "eventId": 44,
        },
    ]

    response = client.post("/webhook/hubspot/inbound", json=payload, headers=_auth_headers())

    assert response.status_code == 200
    assert [item["status"] for item in response.json()["results"]] == ["queued", "duplicate"]
    with SessionLocal() as session:
        job = session.query(InboundJob).one()
        assert job.event_key == "hubspot:ticket:811:changed:44"


def test_ticket_stage_change_away_from_new_is_ignored(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "HUBSPOT_TICKET_STAGE_NEW", "new-stage")
    response = client.post(
        "/webhook/hubspot/inbound",
        json=[{
            "subscriptionType": "ticket.propertyChange",
            "objectId": 812,
            "propertyName": "hs_pipeline_stage",
            "propertyValue": "closed-stage",
            "eventId": 45,
        }],
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "ignored"


# ---------- Error handling ----------


@patch("src.agents.inbound.InboundAgent.handle", side_effect=Exception("boom"))
def test_duplicate_event_is_acknowledged_without_processing(mock_handle, client: TestClient) -> None:
    payload = [
        {"subscriptionType": "ticket.creation", "objectId": 701, "occurredAt": 1684000020000},
        {"subscriptionType": "ticket.creation", "objectId": 701, "occurredAt": 1684000021000},
    ]
    r = client.post(
        "/webhook/hubspot/inbound",
        json=payload,
        headers=_auth_headers(),
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["status"] == "queued"
    assert results[1]["status"] == "duplicate"
    mock_handle.assert_not_called()


def test_invalid_json_returns_400(client: TestClient) -> None:
    r = client.post(
        "/webhook/hubspot/inbound",
        content=b"not-json",
        headers={**_auth_headers(), "Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_body_size_is_capped_before_parsing(client: TestClient) -> None:
    r = client.post(
        "/webhook/hubspot/inbound",
        content=b"x" * (MAX_WEBHOOK_BODY_BYTES + 1),
        headers={**_auth_headers(), "Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_batch_size_is_capped(client: TestClient) -> None:
    payload = [
        {"subscriptionType": "ticket.creation", "objectId": index}
        for index in range(MAX_WEBHOOK_EVENTS + 1)
    ]
    r = client.post("/webhook/hubspot/inbound", json=payload, headers=_auth_headers())
    assert r.status_code == 413


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
