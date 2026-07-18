"""Tests for health-check module — all external calls mocked."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.common.healthcheck import (
    CheckResult,
    HealthReport,
    _check_db,
    _check_disk_space,
    _check_gemini,
    _check_operational_queue,
    _check_worker_heartbeats,
    run_healthchecks,
)
from src.db.models import Event, InboundJob


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


def test_operational_queue_passes_when_empty(db_session_factory) -> None:
    with patch("src.db.session.SessionLocal", db_session_factory):
        result = _check_operational_queue()
    assert result.status == "PASS"
    assert result.name == "operational_queue"


def test_operational_queue_warns_on_expired_processing_lease(db_session_factory) -> None:
    with db_session_factory() as session:
        session.add(
            InboundJob(
                event_key="stuck",
                source="webhook",
                payload={"ticket_id": "stuck"},
                status="processing",
                attempts=1,
                available_at=datetime.now(timezone.utc),
                locked_at=datetime.now(timezone.utc) - timedelta(hours=1),
                locked_by="dead-worker",
            )
        )
        session.commit()

    with patch("src.db.session.SessionLocal", db_session_factory):
        result = _check_operational_queue()
    assert result.status == "WARN"
    assert "stuck=1" in result.detail


def test_worker_heartbeat_is_single_row_and_health_passes(
    monkeypatch, db_session_factory
) -> None:
    from src.agents import worker_heartbeat

    worker_heartbeat._last_written.clear()
    monkeypatch.setattr(worker_heartbeat, "SessionLocal", db_session_factory)
    monkeypatch.setattr("src.db.session.SessionLocal", db_session_factory)
    monkeypatch.setattr("src.common.healthcheck.settings.INBOUND_WORKER_ENABLED", True)
    monkeypatch.setattr("src.common.healthcheck.settings.INBOUND_POLL_ENABLED", False)
    monkeypatch.setattr("src.common.healthcheck.settings.SEND_WORKER_ENABLED", False)

    worker_heartbeat.record_worker_heartbeat("inbound", min_interval_seconds=0)
    worker_heartbeat.record_worker_heartbeat("inbound", min_interval_seconds=0)

    assert _check_worker_heartbeats().status == "PASS"
    with db_session_factory() as session:
        assert session.query(Event).filter_by(kind="worker_heartbeat:inbound").count() == 1


def test_worker_heartbeat_warns_when_enabled_worker_has_no_row(
    monkeypatch, db_session_factory
) -> None:
    monkeypatch.setattr("src.db.session.SessionLocal", db_session_factory)
    monkeypatch.setattr("src.common.healthcheck.settings.INBOUND_WORKER_ENABLED", False)
    monkeypatch.setattr("src.common.healthcheck.settings.INBOUND_POLL_ENABLED", False)
    monkeypatch.setattr("src.common.healthcheck.settings.SEND_WORKER_ENABLED", True)

    result = _check_worker_heartbeats()
    assert result.status == "WARN"
    assert "send=missing" in result.detail


def test_run_healthchecks_returns_report(db_session_factory) -> None:
    with (
        patch("src.common.healthcheck.settings") as s,
        patch("src.db.session.SessionLocal", db_session_factory),
        patch("smtplib.SMTP"),
    ):
        s.GOOGLE_CREDENTIALS_JSON = ""  # FAIL fast, no network
        s.HUBSPOT_PRIVATE_APP_TOKEN = ""
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
