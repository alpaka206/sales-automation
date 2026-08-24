"""Additional healthcheck tests for HubSpot delivery and send quota."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import respx

from src.common.healthcheck import (
    CheckResult,
    _check_hubspot,
    _check_hubspot_conversations,
    _check_send_quota,
    run_healthchecks,
)


@respx.mock
def test_check_hubspot_pass() -> None:
    respx.get("https://api.hubapi.com/crm/v3/objects/contacts").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    with patch("src.common.healthcheck.settings") as configured:
        configured.HUBSPOT_PRIVATE_APP_TOKEN = "test-token"
        result = _check_hubspot()
    assert result.status == "PASS"


@respx.mock
def test_check_hubspot_auth_fail() -> None:
    respx.get("https://api.hubapi.com/crm/v3/objects/contacts").mock(
        return_value=httpx.Response(401, json={"message": "unauthorized"})
    )
    with patch("src.common.healthcheck.settings") as configured:
        configured.HUBSPOT_PRIVATE_APP_TOKEN = "bad-token"
        result = _check_hubspot()
    assert result.status == "FAIL"
    assert "Auth" in result.detail


@respx.mock
def test_check_hubspot_server_error_is_warning() -> None:
    respx.get("https://api.hubapi.com/crm/v3/objects/contacts").mock(
        return_value=httpx.Response(500, json={})
    )
    with patch("src.common.healthcheck.settings") as configured:
        configured.HUBSPOT_PRIVATE_APP_TOKEN = "test-token"
        result = _check_hubspot()
    assert result.status == "WARN"


@respx.mock
def test_check_hubspot_connection_error() -> None:
    respx.get("https://api.hubapi.com/crm/v3/objects/contacts").mock(
        side_effect=httpx.ConnectError("no connection")
    )
    with patch("src.common.healthcheck.settings") as configured:
        configured.HUBSPOT_PRIVATE_APP_TOKEN = "test-token"
        result = _check_hubspot()
    assert result.status == "FAIL"


@respx.mock
def test_check_hubspot_conversations_pass() -> None:
    respx.get(
        "https://api.hubapi.com/conversations/v3/conversations/actors/A-82843387"
    ).mock(
        return_value=httpx.Response(
            200, json={"id": "A-82843387", "type": "AGENT"}
        )
    )
    respx.get(
        "https://api.hubapi.com/conversations/v3/conversations/channel-accounts/2039804092"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "channelId": "1002",
                "active": True,
                "authorized": True,
                "archived": False,
            },
        )
    )
    with patch("src.common.healthcheck.settings") as configured:
        configured.HUBSPOT_PRIVATE_APP_TOKEN = "test-token"
        configured.HUBSPOT_SENDER_ACTOR_ID = "A-82843387"
        configured.HUBSPOT_DEFAULT_EMAIL_CHANNEL_ACCOUNT_ID = "2039804092"
        result = _check_hubspot_conversations()
    assert result.status == "PASS"


def test_check_hubspot_conversations_requires_delivery_config() -> None:
    with patch("src.common.healthcheck.settings") as configured:
        configured.HUBSPOT_SENDER_ACTOR_ID = ""
        configured.HUBSPOT_DEFAULT_EMAIL_CHANNEL_ACCOUNT_ID = ""
        result = _check_hubspot_conversations()
    assert result.status == "FAIL"


def test_check_send_quota_pass() -> None:
    with (
        patch("src.common.healthcheck.settings") as configured,
        patch("src.agents.send_worker.get_daily_count", return_value=5),
    ):
        configured.DAILY_SEND_LIMIT = 100
        result = _check_send_quota()
    assert result.status == "PASS"
    assert "5/100" in result.detail


def test_check_send_quota_limit_hit() -> None:
    with (
        patch("src.common.healthcheck.settings") as configured,
        patch("src.agents.send_worker.get_daily_count", return_value=100),
    ):
        configured.DAILY_SEND_LIMIT = 100
        result = _check_send_quota()
    assert result.status == "WARN"


def test_run_healthchecks_includes_core_checks(db_session_factory) -> None:
    with (
        patch("src.common.healthcheck.settings") as configured,
        patch("src.db.session.SessionLocal", db_session_factory),
        patch(
            "src.common.healthcheck._check_google_sheets",
            return_value=CheckResult(
                name="google_sheets", status="PASS", detail="patched"
            ),
        ),
        patch("src.llm.providers.gemini_vertex.call_gemini"),
    ):
        configured.GOOGLE_CREDENTIALS_JSON = '{"project_id": "p"}'
        configured.HUBSPOT_PRIVATE_APP_TOKEN = ""
        configured.SEND_WORKER_ENABLED = False
        report = run_healthchecks()
    names = [check.name for check in report.checks]
    assert "Gemini (Vertex)" in names
    assert "smtp_login" not in names


def test_run_healthchecks_with_send_worker(db_session_factory) -> None:
    with (
        patch("src.common.healthcheck.settings") as configured,
        patch("src.db.session.SessionLocal", db_session_factory),
        patch(
            "src.common.healthcheck._check_google_sheets",
            return_value=CheckResult(
                name="google_sheets", status="PASS", detail="patched"
            ),
        ),
        patch("src.llm.providers.gemini_vertex.call_gemini"),
        patch("src.agents.send_worker.get_daily_count", return_value=0),
    ):
        configured.GOOGLE_CREDENTIALS_JSON = '{"project_id": "p"}'
        configured.HUBSPOT_PRIVATE_APP_TOKEN = ""
        configured.SEND_WORKER_ENABLED = True
        configured.DAILY_SEND_LIMIT = 100
        report = run_healthchecks()
    assert "send_quota" in [check.name for check in report.checks]
