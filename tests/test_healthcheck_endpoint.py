"""Tests for the /internal/healthcheck endpoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.common.config import settings
from src.common.healthcheck import CheckResult, HealthReport


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _fake_report(overall: str = "PASS", checks: list[CheckResult] | None = None) -> HealthReport:
    if checks is None:
        checks = [CheckResult(name="db_connectivity", status="PASS", detail="OK", latency_ms=1)]
    return HealthReport(checks=checks, overall_status=overall)


def test_healthcheck_endpoint_requires_token(client: TestClient) -> None:
    r = client.post("/internal/healthcheck")
    assert r.status_code == 401


@patch("src.common.healthcheck.run_healthchecks")
def test_healthcheck_endpoint_returns_report(mock_hc, client: TestClient) -> None:
    mock_hc.return_value = _fake_report()
    r = client.post(
        "/internal/healthcheck",
        headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["overall_status"] == "PASS"
    assert len(data["checks"]) == 1
    assert data["checks"][0]["name"] == "db_connectivity"


@patch("src.common.healthcheck.run_healthchecks")
def test_healthcheck_endpoint_with_failures(mock_hc, client: TestClient) -> None:
    mock_hc.return_value = _fake_report(
        overall="FAIL",
        checks=[
            CheckResult(name="db_connectivity", status="PASS", detail="OK"),
            CheckResult(name="claude_cli_token", status="FAIL", detail="Token expired"),
        ],
    )
    r = client.post(
        "/internal/healthcheck",
        headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["overall_status"] == "FAIL"
    failed = [c for c in data["checks"] if c["status"] == "FAIL"]
    assert len(failed) == 1
    assert failed[0]["name"] == "claude_cli_token"
