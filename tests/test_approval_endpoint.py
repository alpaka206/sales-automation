"""Tests for the wired POST /approve/{message_id} endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.agents.approval import ApprovalError
from src.api.main import app
from src.common.config import settings

TOKEN_HEADER = {"X-Internal-Token": settings.INTERNAL_API_TOKEN}


@pytest.fixture(autouse=True)
def _disable_approval_token_and_send_worker():
    with patch.object(settings, "APPROVAL_REQUIRE_TOKEN", False), \
         patch.object(settings, "SEND_WORKER_ENABLED", False):
        yield


def _make_message(
    id: int = 1,
    status: str = "approved",
    body: str = "Hello",
    subject: str = "Subject",
    conversation_contact_id: int = 99,
) -> MagicMock:
    conv = MagicMock()
    conv.contact_id = conversation_contact_id
    msg = MagicMock()
    msg.id = id
    msg.status = status
    msg.body = body
    msg.subject = subject
    msg.conversation = conv
    return msg


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# ---- approve flow ----


@patch("src.api.main.approve")
@patch("src.agents.send_worker.send_approved_now", new_callable=AsyncMock, return_value=True)
def test_approve_sets_status_to_sent(mock_send_now, mock_approve, client):
    msg = _make_message(status="approved")
    mock_approve.return_value = msg

    r = client.post(
        "/approve/1",
        json={"approver": "slack:U001", "action": "approve"},
        headers=TOKEN_HEADER,
    )

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "sent"
    assert data["message_id"] == 1

    mock_approve.assert_called_once_with(1, "slack:U001", None)
    mock_send_now.assert_awaited_once_with(1)


# ---- edit flow ----


@patch("src.api.main.approve")
@patch("src.agents.send_worker.send_approved_now", new_callable=AsyncMock, return_value=True)
def test_edit_updates_body_then_sends(mock_send_now, mock_approve, client):
    msg = _make_message(status="approved", body="Edited body")
    mock_approve.return_value = msg

    r = client.post(
        "/approve/1",
        json={"approver": "slack:U002", "action": "edit", "edited_body": "Edited body"},
        headers=TOKEN_HEADER,
    )

    assert r.status_code == 200
    assert r.json()["status"] == "sent"
    mock_approve.assert_called_once_with(1, "slack:U002", "Edited body")
    mock_send_now.assert_awaited_once_with(1)


# ---- reject flow ----


@patch("src.api.main.reject")
def test_reject_sets_status_rejected(mock_reject, client):
    msg = _make_message(id=2, status="rejected")
    mock_reject.return_value = msg

    r = client.post(
        "/approve/2",
        json={"approver": "slack:U003", "action": "reject", "reason": "Off topic"},
        headers=TOKEN_HEADER,
    )

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "rejected"
    assert data["message_id"] == 2
    mock_reject.assert_called_once_with(2, "slack:U003", "Off topic")


# ---- error: not found / not pending ----


@patch("src.api.main.approve", side_effect=ApprovalError("Message 99 not found."))
def test_invalid_message_returns_400(mock_approve, client):
    r = client.post(
        "/approve/99",
        json={"approver": "slack:U001", "action": "approve"},
        headers=TOKEN_HEADER,
    )
    assert r.status_code == 400
    assert "not found" in r.json()["detail"]


@patch(
    "src.api.main.approve",
    side_effect=ApprovalError("Message 1 is sent, not pending_approval."),
)
def test_double_approve_returns_400(mock_approve, client):
    r = client.post(
        "/approve/1",
        json={"approver": "slack:U001", "action": "approve"},
        headers=TOKEN_HEADER,
    )
    assert r.status_code == 400
    assert "not pending_approval" in r.json()["detail"]


# ---- send failure → 500 ----


@patch("src.api.main.approve")
@patch("src.agents.send_worker.send_approved_now", new_callable=AsyncMock, return_value=False)
def test_send_failure_returns_500(mock_send_now, mock_approve, client):
    mock_approve.return_value = _make_message()

    r = client.post(
        "/approve/1",
        json={"approver": "slack:U001", "action": "approve"},
        headers=TOKEN_HEADER,
    )
    assert r.status_code == 500
    assert "Send failed" in r.json()["detail"]
