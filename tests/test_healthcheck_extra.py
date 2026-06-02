"""Additional healthcheck tests — SMTP, HubSpot, send quota, Gemini, overall status."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.common.healthcheck import (
    _check_hubspot,
    _check_send_quota,
    _check_smtp,
    _smtp_provider_label,
    run_healthchecks,
)


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


# ---------- run_healthchecks ----------


def test_run_healthchecks_includes_gemini(db_session_factory) -> None:
    with patch("src.common.healthcheck.settings") as s, \
         patch("src.db.session.SessionLocal", db_session_factory), \
         patch("src.llm.providers.gemini_vertex.call_gemini"):
        s.GOOGLE_CREDENTIALS_JSON = '{"project_id": "p"}'
        s.HUBSPOT_PRIVATE_APP_TOKEN = ""
        s.EMAIL_PROVIDER = "hubspot"
        s.SEND_WORKER_ENABLED = False

        report = run_healthchecks()

    names = [c.name for c in report.checks]
    assert "Gemini (Vertex)" in names


@patch("smtplib.SMTP")
def test_run_healthchecks_with_smtp(mock_smtp, db_session_factory) -> None:
    mock_smtp.return_value = MagicMock()

    with patch("src.common.healthcheck.settings") as s, \
         patch("src.db.session.SessionLocal", db_session_factory), \
         patch("src.llm.providers.gemini_vertex.call_gemini"):
        s.GOOGLE_CREDENTIALS_JSON = '{"project_id": "p"}'
        s.HUBSPOT_PRIVATE_APP_TOKEN = ""
        s.EMAIL_PROVIDER = "smtp"
        s.SMTP_HOST = "smtp.gmail.com"
        s.SMTP_PORT = 587
        s.SMTP_USERNAME = ""
        s.SMTP_PASSWORD = ""
        s.SEND_WORKER_ENABLED = False

        report = run_healthchecks()

    names = [c.name for c in report.checks]
    assert "smtp_login" in names


def test_run_healthchecks_with_send_worker(db_session_factory) -> None:
    with patch("src.common.healthcheck.settings") as s, \
         patch("src.db.session.SessionLocal", db_session_factory), \
         patch("src.llm.providers.gemini_vertex.call_gemini"), \
         patch("src.agents.send_worker.get_daily_count", return_value=0):
        s.GOOGLE_CREDENTIALS_JSON = '{"project_id": "p"}'
        s.HUBSPOT_PRIVATE_APP_TOKEN = ""
        s.EMAIL_PROVIDER = "hubspot"
        s.SEND_WORKER_ENABLED = True
        s.DAILY_SEND_LIMIT = 100

        report = run_healthchecks()

    names = [c.name for c in report.checks]
    assert "send_quota" in names


def test_run_healthchecks_overall_warn(db_session_factory) -> None:
    with patch("src.common.healthcheck.settings") as s, \
         patch("src.db.session.SessionLocal", db_session_factory), \
         patch("src.common.healthcheck.shutil.disk_usage") as mock_du, \
         patch("src.llm.providers.gemini_vertex.call_gemini"):
        s.GOOGLE_CREDENTIALS_JSON = '{"project_id": "p"}'
        s.HUBSPOT_PRIVATE_APP_TOKEN = ""
        s.EMAIL_PROVIDER = "hubspot"
        s.SEND_WORKER_ENABLED = False

        mock_du.return_value = MagicMock(free=100 * 1024 * 1024)
        report = run_healthchecks()

    assert report.overall_status == "WARN"
