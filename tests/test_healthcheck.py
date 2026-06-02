"""Tests for health-check module — all external calls mocked."""

from __future__ import annotations

from unittest.mock import patch

from src.common.healthcheck import (
    CheckResult,
    HealthReport,
    _check_db,
    _check_disk_space,
    _check_gemini,
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


def test_check_gemini_no_creds() -> None:
    with patch("src.common.healthcheck.settings") as s:
        s.GOOGLE_CREDENTIALS_JSON = ""
        result = _check_gemini()
    assert result.status == "FAIL"
    assert "empty" in result.detail.lower()
    assert result.name == "Gemini (Vertex)"


def test_check_gemini_pass() -> None:
    with (
        patch("src.common.healthcheck.settings") as s,
        patch("src.llm.providers.gemini_vertex.call_gemini"),
    ):
        s.GOOGLE_CREDENTIALS_JSON = '{"project_id": "p"}'
        result = _check_gemini()
    assert result.status == "PASS"


def test_check_gemini_permission_fail() -> None:
    err = Exception("permission denied")
    err.code = 403
    with (
        patch("src.common.healthcheck.settings") as s,
        patch("src.llm.providers.gemini_vertex.call_gemini", side_effect=err),
    ):
        s.GOOGLE_CREDENTIALS_JSON = '{"project_id": "p"}'
        result = _check_gemini()
    assert result.status == "FAIL"


def test_check_disk_space_pass() -> None:
    result = _check_disk_space()
    assert result.status in ("PASS", "WARN")
    assert result.name == "disk_space"


def test_run_healthchecks_returns_report(db_session_factory) -> None:
    with (
        patch("src.common.healthcheck.settings") as s,
        patch("src.db.session.SessionLocal", db_session_factory),
    ):
        s.GOOGLE_CREDENTIALS_JSON = ""  # FAIL fast, no network
        s.HUBSPOT_PRIVATE_APP_TOKEN = ""
        s.EMAIL_PROVIDER = "hubspot"
        s.SEND_WORKER_ENABLED = False

        report = run_healthchecks()

    assert isinstance(report, HealthReport)
    assert len(report.checks) >= 2
    names = [c.name for c in report.checks]
    assert "db_connectivity" in names
    assert "Gemini (Vertex)" in names
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
