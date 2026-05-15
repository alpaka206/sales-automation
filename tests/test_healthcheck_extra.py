"""Additional healthcheck tests — SMTP, HubSpot, send quota, anthropic API, overall status."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from src.common.healthcheck import (
    _check_anthropic_api,
    _check_hubspot,
    _check_send_quota,
    _check_smtp,
    _smtp_provider_label,
    run_healthchecks,
)


@pytest.fixture(autouse=True)
def _ensure_anthropic():
    """Inject a fake anthropic module if it's not installed."""
    had_it = "anthropic" in sys.modules
    old_mod = sys.modules.get("anthropic")
    if not had_it:
        mod = ModuleType("anthropic")
        mod.Anthropic = MagicMock()
        sys.modules["anthropic"] = mod
    yield
    if not had_it:
        sys.modules.pop("anthropic", None)
    else:
        sys.modules["anthropic"] = old_mod


# ---------- _smtp_provider_label ----------


def test_smtp_provider_label_gmail() -> None:
    with patch("src.common.healthcheck.settings") as s:
        s.SMTP_HOST = "smtp.gmail.com"
        assert _smtp_provider_label() == "Gmail"


def test_smtp_provider_label_unknown() -> None:
    with patch("src.common.healthcheck.settings") as s:
        s.SMTP_HOST = "mail.custom.co"
        assert _smtp_provider_label() == "mail.custom.co"


def test_smtp_provider_label_empty() -> None:
    with patch("src.common.healthcheck.settings") as s:
        s.SMTP_HOST = ""
        assert _smtp_provider_label() == "unknown"


# ---------- _check_smtp ----------


@patch("smtplib.SMTP")
def test_check_smtp_pass(mock_smtp_cls) -> None:
    mock_server = MagicMock()
    mock_smtp_cls.return_value = mock_server

    with patch("src.common.healthcheck.settings") as s:
        s.SMTP_HOST = "smtp.gmail.com"
        s.SMTP_PORT = 587
        s.SMTP_USERNAME = "user"
        s.SMTP_PASSWORD = "pass"
        result = _check_smtp()

    assert result.status == "PASS"
    assert "Gmail" in result.detail
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("user", "pass")


@patch("smtplib.SMTP", side_effect=ConnectionError("refused"))
def test_check_smtp_fail(mock_smtp_cls) -> None:
    with patch("src.common.healthcheck.settings") as s:
        s.SMTP_HOST = "smtp.gmail.com"
        s.SMTP_PORT = 587
        s.SMTP_USERNAME = ""
        s.SMTP_PASSWORD = ""
        result = _check_smtp()

    assert result.status == "FAIL"


# ---------- _check_hubspot ----------


def test_check_hubspot_pass() -> None:
    import httpx
    import respx

    with respx.mock:
        respx.get("https://api.hubapi.com/crm/v3/objects/contacts").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        with patch("src.common.healthcheck.settings") as s:
            s.HUBSPOT_PRIVATE_APP_TOKEN = "test-token"
            result = _check_hubspot()

    assert result.status == "PASS"


def test_check_hubspot_auth_fail() -> None:
    import httpx
    import respx

    with respx.mock:
        respx.get("https://api.hubapi.com/crm/v3/objects/contacts").mock(
            return_value=httpx.Response(401, json={"message": "unauthorized"})
        )
        with patch("src.common.healthcheck.settings") as s:
            s.HUBSPOT_PRIVATE_APP_TOKEN = "bad-token"
            result = _check_hubspot()

    assert result.status == "FAIL"
    assert "Auth" in result.detail


def test_check_hubspot_server_error() -> None:
    import httpx
    import respx

    with respx.mock:
        respx.get("https://api.hubapi.com/crm/v3/objects/contacts").mock(
            return_value=httpx.Response(500, json={})
        )
        with patch("src.common.healthcheck.settings") as s:
            s.HUBSPOT_PRIVATE_APP_TOKEN = "token"
            result = _check_hubspot()

    assert result.status == "WARN"


def test_check_hubspot_connection_error() -> None:
    import httpx
    import respx

    with respx.mock:
        respx.get("https://api.hubapi.com/crm/v3/objects/contacts").mock(
            side_effect=httpx.ConnectError("no connection")
        )
        with patch("src.common.healthcheck.settings") as s:
            s.HUBSPOT_PRIVATE_APP_TOKEN = "token"
            result = _check_hubspot()

    assert result.status == "FAIL"


# ---------- _check_anthropic_api with mock SDK ----------


def test_check_anthropic_api_pass() -> None:
    mock_client = MagicMock()
    mock_usage = MagicMock()
    mock_usage.input_tokens = 1
    mock_usage.output_tokens = 1
    mock_response = MagicMock(usage=mock_usage)
    mock_client.messages.create.return_value = mock_response

    fake_anthropic = sys.modules["anthropic"]
    fake_anthropic.Anthropic = MagicMock(return_value=mock_client)

    with patch("src.common.healthcheck.settings") as s:
        s.ANTHROPIC_API_KEY = "sk-test"
        s.ANTHROPIC_MODEL = "claude-sonnet-4-6"
        result = _check_anthropic_api()

    assert result.status == "PASS"


def test_check_anthropic_api_401() -> None:
    exc = Exception("Authentication error")
    exc.status_code = 401

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = exc

    fake_anthropic = sys.modules["anthropic"]
    fake_anthropic.Anthropic = MagicMock(return_value=mock_client)

    with patch("src.common.healthcheck.settings") as s:
        s.ANTHROPIC_API_KEY = "sk-bad"
        s.ANTHROPIC_MODEL = "claude-sonnet-4-6"
        result = _check_anthropic_api()

    assert result.status == "FAIL"
    assert "401" in result.detail


def test_check_anthropic_api_429() -> None:
    exc = Exception("Rate limited")
    exc.status_code = 429

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = exc

    fake_anthropic = sys.modules["anthropic"]
    fake_anthropic.Anthropic = MagicMock(return_value=mock_client)

    with patch("src.common.healthcheck.settings") as s:
        s.ANTHROPIC_API_KEY = "sk-key"
        s.ANTHROPIC_MODEL = "claude-sonnet-4-6"
        result = _check_anthropic_api()

    assert result.status == "WARN"
    assert "429" in result.detail


# ---------- _check_send_quota ----------


def test_check_send_quota_pass() -> None:
    with patch("src.common.healthcheck.settings") as s, \
         patch("src.agents.send_worker.get_daily_count", return_value=5):
        s.DAILY_SEND_LIMIT = 100
        s.SEND_WORKER_ENABLED = True
        result = _check_send_quota()

    assert result.status == "PASS"
    assert "5/100" in result.detail


def test_check_send_quota_limit_hit() -> None:
    with patch("src.common.healthcheck.settings") as s, \
         patch("src.agents.send_worker.get_daily_count", return_value=100):
        s.DAILY_SEND_LIMIT = 100
        s.SEND_WORKER_ENABLED = True
        result = _check_send_quota()

    assert result.status == "WARN"


# ---------- run_healthchecks with various providers ----------


def test_run_healthchecks_anthropic_provider(db_session_factory) -> None:
    mock_client = MagicMock()
    mock_usage = MagicMock(input_tokens=1, output_tokens=1)
    mock_client.messages.create.return_value = MagicMock(usage=mock_usage)

    fake_anthropic = sys.modules["anthropic"]
    fake_anthropic.Anthropic = MagicMock(return_value=mock_client)

    with patch("src.common.healthcheck.settings") as s, \
         patch("src.db.session.SessionLocal", db_session_factory):
        s.LLM_PROVIDER = "anthropic_api"
        s.ANTHROPIC_API_KEY = "sk-test"
        s.ANTHROPIC_MODEL = "claude-sonnet-4-6"
        s.HUBSPOT_PRIVATE_APP_TOKEN = ""
        s.EMAIL_PROVIDER = "hubspot"
        s.SEND_WORKER_ENABLED = False

        report = run_healthchecks()

    names = [c.name for c in report.checks]
    assert "anthropic_api_key" in names
    assert "claude_cli_token" not in names


@patch("smtplib.SMTP")
def test_run_healthchecks_with_smtp(mock_smtp, db_session_factory) -> None:
    mock_smtp.return_value = MagicMock()

    with patch("src.common.healthcheck.settings") as s, \
         patch("src.db.session.SessionLocal", db_session_factory), \
         patch("src.common.healthcheck.subprocess.run") as mock_run:
        s.LLM_PROVIDER = "claude_cli"
        s.CLAUDE_CLI_PATH = "claude"
        s.HUBSPOT_PRIVATE_APP_TOKEN = ""
        s.EMAIL_PROVIDER = "smtp"
        s.SMTP_HOST = "smtp.gmail.com"
        s.SMTP_PORT = 587
        s.SMTP_USERNAME = ""
        s.SMTP_PASSWORD = ""
        s.SEND_WORKER_ENABLED = False

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        report = run_healthchecks()

    names = [c.name for c in report.checks]
    assert "smtp_login" in names


def test_run_healthchecks_with_send_worker(db_session_factory) -> None:
    with patch("src.common.healthcheck.settings") as s, \
         patch("src.db.session.SessionLocal", db_session_factory), \
         patch("src.common.healthcheck.subprocess.run") as mock_run, \
         patch("src.agents.send_worker.get_daily_count", return_value=0):
        s.LLM_PROVIDER = "claude_cli"
        s.CLAUDE_CLI_PATH = "claude"
        s.HUBSPOT_PRIVATE_APP_TOKEN = ""
        s.EMAIL_PROVIDER = "hubspot"
        s.SEND_WORKER_ENABLED = True
        s.DAILY_SEND_LIMIT = 100

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        report = run_healthchecks()

    names = [c.name for c in report.checks]
    assert "send_quota" in names


def test_run_healthchecks_overall_warn(db_session_factory) -> None:
    with patch("src.common.healthcheck.settings") as s, \
         patch("src.db.session.SessionLocal", db_session_factory), \
         patch("src.common.healthcheck.shutil.disk_usage") as mock_du, \
         patch("src.common.healthcheck.subprocess.run") as mock_run:
        s.LLM_PROVIDER = "claude_cli"
        s.CLAUDE_CLI_PATH = "claude"
        s.HUBSPOT_PRIVATE_APP_TOKEN = ""
        s.EMAIL_PROVIDER = "hubspot"
        s.SEND_WORKER_ENABLED = False

        mock_du.return_value = MagicMock(free=100 * 1024 * 1024)
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        report = run_healthchecks()

    assert report.overall_status == "WARN"
