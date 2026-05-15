"""Tests for web UI infrastructure — dashboard route and auth bypass."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app


def _client() -> TestClient:
    return TestClient(app)


def test_dashboard_returns_200():
    """GET / from localhost must return 200 with expected content."""
    r = _client().get("/")
    assert r.status_code == 200
    assert "Sales Automation" in r.text


def test_dashboard_has_tailwind_cdn():
    """base.html must load Tailwind via CDN."""
    r = _client().get("/")
    assert "cdn.tailwindcss.com" in r.text


def test_dashboard_has_htmx():
    """base.html must load HTMX via CDN."""
    r = _client().get("/")
    assert "htmx.org" in r.text


def test_dashboard_has_korean_font():
    """base.html must load Noto Sans KR."""
    r = _client().get("/")
    assert "Noto+Sans+KR" in r.text or "Noto Sans KR" in r.text
