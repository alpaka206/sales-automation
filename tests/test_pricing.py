"""토큰 추정과 단가표.

**사용량 기록 테스트는 없습니다.** ``log_usage``/``get_usage_since`` 와 ``llm_usage`` 표는
2026-08-27 에 나갔습니다(마이그레이션 0095) — 호출마다 한 줄씩 쌓는데 읽는 화면이 없었습니다.
여기 남은 것은 상태 없는 계산뿐입니다.
"""

from __future__ import annotations

from src.llm.pricing import LLMResult, estimate_cost, estimate_tokens, format_cost


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


def test_usage_recording_is_gone() -> None:
    """기록을 그만둔 것은 결정입니다. 되살아나면 다시 쌓이기만 하는 표가 됩니다 — 되살릴
    거면 *어느 화면이 그것을 읽는지*부터 정하세요(0095 의 docstring)."""
    import src.llm.client as client
    import src.llm.pricing as pricing

    assert not hasattr(pricing, "log_usage")
    assert not hasattr(pricing, "get_usage_since")
    assert "log_usage" not in client.__dict__


def test_llm_result_dataclass() -> None:
    r = LLMResult(text="hello", input_tokens=10, output_tokens=5, model="test")
    assert r.text == "hello"
    assert r.input_tokens == 10
    assert r.output_tokens == 5
    assert r.model == "test"
    assert r.cache_read_input_tokens == 0
    assert r.cache_creation_input_tokens == 0
