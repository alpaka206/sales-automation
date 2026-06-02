"""Tests for LLM pricing module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.llm.pricing import (
    LLMResult,
    estimate_cost,
    estimate_tokens,
    format_cost,
    get_usage_since,
    log_usage,
)


def test_estimate_tokens_basic() -> None:
    assert estimate_tokens("hello world!") == 3  # 12 chars / 4
    assert estimate_tokens("") == 1  # min 1


def test_estimate_cost_known_model() -> None:
    cost = estimate_cost("gemini-2.5-flash", input_tokens=1000, output_tokens=500)
    expected = (1000 * 0.30 + 500 * 2.50) / 1_000_000
    assert abs(cost - expected) < 1e-9


def test_estimate_cost_unknown_model_uses_default() -> None:
    cost = estimate_cost("some-future-model", input_tokens=1000, output_tokens=500)
    expected = (1000 * 0.30 + 500 * 2.50) / 1_000_000
    assert abs(cost - expected) < 1e-9


def test_format_cost_small() -> None:
    assert format_cost(0.001234) == "$0.0012"


def test_format_cost_large() -> None:
    assert format_cost(1.50) == "$1.50"


def test_format_cost_zero() -> None:
    assert format_cost(0.0) == "$0.0000"


def test_log_and_get_usage(db_session_factory) -> None:
    with patch("src.db.session.SessionLocal", db_session_factory):
        now = datetime.now(timezone.utc)
        result = LLMResult(text="hi", input_tokens=100, output_tokens=50, model="gemini-2.5-flash")
        log_usage(result, "gemini_vertex")
        log_usage(result, "gemini_vertex")

        usage = get_usage_since(now - timedelta(seconds=10))

    assert usage["calls"] == 2
    assert usage["total_input"] == 200
    assert usage["total_output"] == 100
    assert usage["total_cost"] > 0
    assert "gemini-2.5-flash" in usage["models"]


def test_get_usage_since_filters_old(db_session_factory) -> None:
    from src.db.models import LLMUsage

    session = db_session_factory()
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    recent = datetime.now(timezone.utc)

    session.add(LLMUsage(
        provider="x", model="m", input_tokens=100, output_tokens=50,
        estimated_cost=0.0, created_at=old,
    ))
    session.add(LLMUsage(
        provider="x", model="m", input_tokens=200, output_tokens=80,
        estimated_cost=0.0, created_at=recent,
    ))
    session.commit()
    session.close()

    with patch("src.db.session.SessionLocal", db_session_factory):
        usage = get_usage_since(datetime.now(timezone.utc) - timedelta(hours=1))

    assert usage["calls"] == 1
    assert usage["total_input"] == 200
    assert usage["total_output"] == 80


def test_get_usage_since_empty_db(db_session_factory) -> None:
    with patch("src.db.session.SessionLocal", db_session_factory):
        usage = get_usage_since(datetime.now(timezone.utc) - timedelta(days=1))

    assert usage["calls"] == 0
    assert usage["total_cost"] == 0.0


def test_llm_result_dataclass() -> None:
    r = LLMResult(text="hello", input_tokens=10, output_tokens=5, model="test")
    assert r.text == "hello"
    assert r.input_tokens == 10
    assert r.output_tokens == 5
    assert r.model == "test"
    assert r.cache_read_input_tokens == 0
    assert r.cache_creation_input_tokens == 0
