"""토큰 추정과 단가표.

**사용량은 더 이상 기록하지 않습니다** (2026-08-27 운영자 지시, 마이그레이션 0095).
``log_usage`` 가 모든 LLM 호출마다 ``llm_usage`` 에 한 줄씩 썼는데, 읽는 곳은
``report.get_usage_since`` 하나였고 그건 ``POST /run/report`` 로만 불렸습니다 — 콘솔에
버튼도 스케줄도 없어서 아무도 부르지 않았습니다. 쌓기만 하는 표였습니다.

남은 것은 **계산**뿐입니다: ``estimate_tokens`` 는 프롬프트 길이를 재는 데,
``estimate_cost``/``format_cost`` 는 그 길이를 돈으로 옮기는 데 씁니다. 상태가 없습니다.
같은 숫자를 다시 보고 싶으면 Vertex 콘솔에 있습니다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
logger = logging.getLogger(__name__)

# Rough heuristic: ~4 chars per token. Only used for budget estimates when a
# provider response omits real token counts. Korean packs denser, so this
# over/under-counts somewhat — acceptable since it never feeds billing.
CHARS_PER_TOKEN = 4

# USD per 1M tokens, per model. Used to estimate spend for the usage dashboard.
_DEFAULT_RATES = {"input": 0.30, "output": 2.50}
PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
}


@dataclass
class LLMResult:
    """Wrapper returned by LLM providers."""

    text: str
    input_tokens: int
    output_tokens: int
    model: str
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


def estimate_tokens(text: str) -> int:
    """Rough token count from character length."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated cost in USD."""
    rates = PRICING.get(model, _DEFAULT_RATES)
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def format_cost(amount: float) -> str:
    """Human-readable cost string."""
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"
