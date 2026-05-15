"""Tests for web UI infrastructure — dashboard route and auth bypass."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.main import app


def _client() -> TestClient:
    return TestClient(app)


def _mock_dashboard_context():
    return {
        "recent_messages": [
            {
                "id": 1,
                "status": "sent",
                "category": "pricing_question",
                "subject": "가격 문의",
                "channel": "email",
                "direction": "outgoing",
                "created_at": __import__("datetime").datetime(2026, 1, 1, 12, 0),
            }
        ],
        "status_counts": {
            "pending_approval": 2,
            "approved": 1,
            "sent": 5,
            "bounced": 0,
            "replied": 1,
        },
        "today_sent": 3,
        "daily_limit": 100,
        "category_counts": [("pricing_question", 4), ("purchase_inquiry", 2)],
    }


@patch("src.api.web.routes._dashboard_context", _mock_dashboard_context)
def test_dashboard_returns_200():
    """GET / from localhost must return 200 with expected content."""
    r = _client().get("/")
    assert r.status_code == 200
    assert "Sales Automation" in r.text


@patch("src.api.web.routes._dashboard_context", _mock_dashboard_context)
def test_dashboard_has_tailwind_cdn():
    """base.html must load Tailwind via CDN."""
    r = _client().get("/")
    assert "cdn.tailwindcss.com" in r.text


@patch("src.api.web.routes._dashboard_context", _mock_dashboard_context)
def test_dashboard_has_htmx():
    """base.html must load HTMX via CDN."""
    r = _client().get("/")
    assert "htmx.org" in r.text


@patch("src.api.web.routes._dashboard_context", _mock_dashboard_context)
def test_dashboard_has_korean_font():
    """base.html must load Noto Sans KR."""
    r = _client().get("/")
    assert "Noto+Sans+KR" in r.text or "Noto Sans KR" in r.text


@patch("src.api.web.routes._dashboard_context", _mock_dashboard_context)
def test_dashboard_shows_status_counts():
    """Dashboard must display status counts."""
    r = _client().get("/")
    assert "pending_approval" in r.text
    assert "sent" in r.text


@patch("src.api.web.routes._dashboard_context", _mock_dashboard_context)
def test_dashboard_shows_recent_messages():
    """Dashboard must display recent message subjects."""
    r = _client().get("/")
    assert "가격 문의" in r.text


@patch("src.api.web.routes._dashboard_context", _mock_dashboard_context)
def test_dashboard_shows_category_counts():
    """Dashboard must display category breakdown."""
    r = _client().get("/")
    assert "pricing_question" in r.text


@patch("src.api.web.routes._dashboard_context", _mock_dashboard_context)
def test_dashboard_shows_daily_send_stats():
    """Dashboard must display today's send count and limit."""
    r = _client().get("/")
    assert "오늘 발송" in r.text
    assert "100" in r.text


@patch("src.api.web.routes._dashboard_context", _mock_dashboard_context)
def test_dashboard_message_link():
    """Message row must link to detail page."""
    r = _client().get("/")
    assert "/messages/1" in r.text
