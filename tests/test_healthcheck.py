"""Tests for health-check module — all external calls mocked."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch


from src.common.healthcheck import (
    CheckResult,
    HealthReport,
    _check_claude_cli,
    _check_db,
    _check_disk_space,
    _check_anthropic_api,
    run_healthchecks,
)


def test_check_db_pass(db_session_factory) -> None:
    with patch("src.db.session.SessionLocal", db_session_factory):
        result = _check_db()
    assert result.status == "PASS"
    assert result.name == "db_connectivity"


def test_check_db_fail() -> None:
    def bad_factory():
        raise RuntimeError("no DB")

    with patch("src.db.session.SessionLocal", bad_factory):
        result = _check_db()
    assert result.status == "FAIL"


@patch("src.common.healthcheck.subprocess.run")
def test_check_claude_cli_pass(mock_run) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="pong", stderr="")
    with patch("src.common.healthcheck.settings") as s:
        s.CLAUDE_CLI_PATH = "claude"
        result = _check_claude_cli()
    assert result.status == "PASS"


@patch("src.common.healthcheck.subprocess.run")
def test_check_claude_cli_auth_fail(mock_run) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Not authenticated")
    with patch("src.common.healthcheck.settings") as s:
        s.CLAUDE_CLI_PATH = "claude"
        result = _check_claude_cli()
    assert result.status == "FAIL"
    assert "expired" in result.detail.lower() or "not authenticated" in result.detail.lower()


@patch("src.common.healthcheck.subprocess.run")
def test_check_claude_cli_timeout(mock_run) -> None:
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=10)
    with patch("src.common.healthcheck.settings") as s:
        s.CLAUDE_CLI_PATH = "claude"
        result = _check_claude_cli()
    assert result.status == "FAIL"
    assert "timed out" in result.detail.lower()


@patch("src.common.healthcheck.subprocess.run")
def test_check_claude_cli_not_found(mock_run) -> None:
    mock_run.side_effect = FileNotFoundError()
    with patch("src.common.healthcheck.settings") as s:
        s.CLAUDE_CLI_PATH = "claude"
        result = _check_claude_cli()
    assert result.status == "FAIL"
    assert "not found" in result.detail.lower()


def test_check_anthropic_api_no_key() -> None:
    with patch("src.common.healthcheck.settings") as s:
        s.ANTHROPIC_API_KEY = ""
        result = _check_anthropic_api()
    assert result.status == "FAIL"
    assert "empty" in result.detail.lower()


def test_check_disk_space_pass() -> None:
    result = _check_disk_space()
    assert result.status in ("PASS", "WARN")
    assert result.name == "disk_space"


def test_run_healthchecks_returns_report(db_session_factory) -> None:
    with (
        patch("src.common.healthcheck.settings") as s,
        patch("src.db.session.SessionLocal", db_session_factory),
        patch("src.common.healthcheck.subprocess.run") as mock_run,
    ):
        s.LLM_PROVIDER = "claude_cli"
        s.CLAUDE_CLI_PATH = "claude"
        s.HUBSPOT_PRIVATE_APP_TOKEN = ""
        s.EMAIL_PROVIDER = "hubspot"
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        report = run_healthchecks()

    assert isinstance(report, HealthReport)
    assert len(report.checks) >= 2
    names = [c.name for c in report.checks]
    assert "db_connectivity" in names
    assert "Claude CLI 로그인 상태" in names
    assert report.overall_status in ("PASS", "WARN", "FAIL")


def test_overall_status_fail_on_any_fail() -> None:
    report = HealthReport(
        checks=[
            CheckResult(name="a", status="PASS", detail="ok"),
            CheckResult(name="b", status="FAIL", detail="bad"),
        ],
        overall_status="FAIL",
    )
    assert report.overall_status == "FAIL"
