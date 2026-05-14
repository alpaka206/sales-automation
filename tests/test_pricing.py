"""Tests for LLM pricing module."""

from __future__ import annotations

import json
import os
import tempfile
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
    cost = estimate_cost("claude-sonnet-4-6", input_tokens=1000, output_tokens=500)
    expected = (1000 * 3.00 + 500 * 15.00) / 1_000_000
    assert abs(cost - expected) < 1e-9


def test_estimate_cost_unknown_model_uses_default() -> None:
    cost = estimate_cost("some-future-model", input_tokens=1000, output_tokens=500)
    expected = (1000 * 3.00 + 500 * 15.00) / 1_000_000
    assert abs(cost - expected) < 1e-9


def test_estimate_cost_free_model() -> None:
    cost = estimate_cost("llama3.1:8b", input_tokens=10000, output_tokens=5000)
    assert cost == 0.0


def test_format_cost_small() -> None:
    assert format_cost(0.001234) == "$0.0012"


def test_format_cost_large() -> None:
    assert format_cost(1.50) == "$1.50"


def test_format_cost_zero() -> None:
    assert format_cost(0.0) == "$0.0000"


def test_log_and_get_usage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        usage_file = os.path.join(tmp, "usage.jsonl")
        with patch("src.llm.pricing.USAGE_FILE", usage_file):
            now = datetime.now(timezone.utc)
            result = LLMResult(text="hi", input_tokens=100, output_tokens=50, model="claude-sonnet-4-6")
            log_usage(result, "anthropic_api")
            log_usage(result, "anthropic_api")

            usage = get_usage_since(now - timedelta(seconds=10))

        assert usage["calls"] == 2
        assert usage["total_input"] == 200
        assert usage["total_output"] == 100
        assert usage["total_cost"] > 0
        assert "claude-sonnet-4-6" in usage["models"]


def test_get_usage_since_filters_old() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        usage_file = os.path.join(tmp, "usage.jsonl")
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        new_ts = datetime.now(timezone.utc).isoformat()

        with open(usage_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": old_ts, "provider": "x", "input_tokens": 100, "output_tokens": 50, "model": "m"}) + "\n")
            f.write(json.dumps({"ts": new_ts, "provider": "x", "input_tokens": 200, "output_tokens": 80, "model": "m"}) + "\n")

        with patch("src.llm.pricing.USAGE_FILE", usage_file):
            usage = get_usage_since(datetime.now(timezone.utc) - timedelta(hours=1))

        assert usage["calls"] == 1
        assert usage["total_input"] == 200
        assert usage["total_output"] == 80


def test_get_usage_since_no_file() -> None:
    with patch("src.llm.pricing.USAGE_FILE", "/nonexistent/path.jsonl"):
        usage = get_usage_since(datetime.now(timezone.utc) - timedelta(days=1))

    assert usage["calls"] == 0
    assert usage["total_cost"] == 0.0


def test_llm_result_dataclass() -> None:
    r = LLMResult(text="hello", input_tokens=10, output_tokens=5, model="test")
    assert r.text == "hello"
    assert r.input_tokens == 10
    assert r.output_tokens == 5
    assert r.model == "test"
