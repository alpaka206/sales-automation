"""Tests for the settings page (lazy health check + LLM usage)."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.api.main import app


class _FakeCheck(BaseModel):
    name: str
    status: str
    detail: str
    latency_ms: int = 0


class _FakeReport(BaseModel):
    checks: list[_FakeCheck]
    overall_status: str


def _mock_settings_context():
    # Settings page itself only renders LLM usage now; health checks load lazily
    # via the /settings/healthcheck fragment.
    return {"today_llm": 12, "week_llm": 45, "llm_provider": "gemini_vertex"}


def _client() -> TestClient:
    return TestClient(app)


@patch("src.api.web.routes.settings_page._settings_context", _mock_settings_context)
def test_settings_page_returns_200():
    r = _client().get("/settings")
    assert r.status_code == 200
    assert "설정" in r.text


@patch("src.api.web.routes.settings_page._settings_context", _mock_settings_context)
def test_settings_shows_llm_usage():
    r = _client().get("/settings")
    assert "12" in r.text
    assert "45" in r.text


@patch("src.api.web.routes.settings_page._settings_context", _mock_settings_context)
def test_settings_lazy_loads_healthcheck():
    # The page no longer runs the (slow) checks inline — it pulls them in lazily.
    r = _client().get("/settings")
    assert "/settings/healthcheck" in r.text
    assert "skeleton" in r.text


@patch("src.api.web.routes.settings_page._settings_context", _mock_settings_context)
def test_settings_no_env_vars():
    # Environment variables are no longer exposed on the settings page.
    r = _client().get("/settings")
    assert "환경변수" not in r.text


def test_settings_healthcheck_fragment_shows_checks():
    fake_report = _FakeReport(
        checks=[_FakeCheck(name="Database", status="PASS", detail="ok", latency_ms=3)],
        overall_status="PASS",
    )
    with patch("src.common.healthcheck.run_healthchecks", return_value=fake_report):
        r = _client().get("/settings/healthcheck")
    assert r.status_code == 200
    assert "Database" in r.text
    # Health status renders as a Korean status pill (PASS → 정상) rather than the raw enum.
    assert "정상" in r.text


def test_settings_healthcheck_fragment_gemini_warning():
    fake_report = _FakeReport(
        checks=[
            _FakeCheck(
                name="Gemini (Vertex)",
                status="FAIL",
                detail="GOOGLE_CREDENTIALS_JSON is empty",
                latency_ms=0,
            )
        ],
        overall_status="FAIL",
    )
    with patch("src.common.healthcheck.run_healthchecks", return_value=fake_report):
        r = _client().get("/settings/healthcheck")
    assert r.status_code == 200
    assert "GOOGLE_CREDENTIALS_JSON" in r.text
