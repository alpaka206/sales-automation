"""Tests for the settings page."""

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
    return {
        "checks": [
            {"name": "Database", "status": "PASS", "detail": "ok", "latency_ms": 5},
            {"name": "Claude CLI 로그인 상태", "status": "FAIL", "detail": "not found", "latency_ms": 0},
        ],
        "overall_status": "FAIL",
        "env_vars": [
            {"name": "HUBSPOT_PRIVATE_APP_TOKEN", "value": "pat-***"},
            {"name": "APP_HOST", "value": "127.0.0.1"},
        ],
        "today_llm": 12,
        "week_llm": 45,
        "llm_provider": "claude_cli",
        "llm_ok": False,
    }


def _mock_settings_context_gemini():
    return {
        "checks": [
            {"name": "Database", "status": "PASS", "detail": "ok", "latency_ms": 5},
            {"name": "gemini_api_key", "status": "FAIL", "detail": "GEMINI_API_KEY is empty", "latency_ms": 0},
        ],
        "overall_status": "FAIL",
        "env_vars": [],
        "today_llm": 0,
        "week_llm": 0,
        "llm_provider": "gemini_api",
        "llm_ok": False,
    }


def _client() -> TestClient:
    return TestClient(app)


@patch("src.api.web.routes._settings_context", _mock_settings_context)
def test_settings_page_returns_200():
    r = _client().get("/settings")
    assert r.status_code == 200
    assert "설정" in r.text


@patch("src.api.web.routes._settings_context", _mock_settings_context)
def test_settings_shows_env_vars_masked():
    r = _client().get("/settings")
    assert "HUBSPOT_PRIVATE_APP_TOKEN" in r.text
    assert "pat-***" in r.text


@patch("src.api.web.routes._settings_context", _mock_settings_context)
def test_settings_shows_healthcheck():
    r = _client().get("/settings")
    assert "Database" in r.text
    assert "PASS" in r.text


@patch("src.api.web.routes._settings_context", _mock_settings_context)
def test_settings_shows_claude_cli_warning():
    r = _client().get("/settings")
    assert "Claude CLI" in r.text
    assert "claude /login" in r.text


@patch("src.api.web.routes._settings_context", _mock_settings_context_gemini)
def test_settings_shows_gemini_warning():
    r = _client().get("/settings")
    assert "GEMINI_API_KEY" in r.text


@patch("src.api.web.routes._settings_context", _mock_settings_context)
def test_settings_shows_llm_usage():
    r = _client().get("/settings")
    assert "12" in r.text
    assert "45" in r.text


def test_settings_refresh_healthcheck():
    fake_report = _FakeReport(
        checks=[_FakeCheck(name="DB", status="PASS", detail="ok", latency_ms=3)],
        overall_status="PASS",
    )
    with patch("src.common.healthcheck.run_healthchecks", return_value=fake_report):
        r = _client().post("/settings/refresh-healthcheck")
    assert r.status_code == 200
    assert "DB" in r.text
