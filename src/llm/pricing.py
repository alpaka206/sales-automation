"""LLM token usage tracking and cost estimation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func

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


def log_usage(result: LLMResult, provider: str) -> None:
    """Write usage record to llm_usage table."""
    try:
        from ..db.models import LLMUsage
        from ..db.session import SessionLocal

        cost = estimate_cost(result.model, result.input_tokens, result.output_tokens)
        session = SessionLocal()
        session.add(
            LLMUsage(
                provider=provider,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cache_read_input_tokens=result.cache_read_input_tokens,
                cache_creation_input_tokens=result.cache_creation_input_tokens,
                estimated_cost=cost,
            )
        )
        session.commit()
        session.close()
    except Exception:
        logger.debug("Failed to log LLM usage.", exc_info=True)


def get_usage_since(since: datetime) -> dict:
    """Aggregate usage from the llm_usage table since a given time."""
    empty = {"models": {}, "total_input": 0, "total_output": 0, "total_cost": 0.0, "calls": 0}
    try:
        from ..db.models import LLMUsage
        from ..db.session import SessionLocal

        threshold = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
        session = SessionLocal()
        rows = (
            session.query(
                LLMUsage.model,
                func.sum(LLMUsage.input_tokens).label("inp"),
                func.sum(LLMUsage.output_tokens).label("out"),
                func.count(LLMUsage.id).label("cnt"),
            )
            .filter(LLMUsage.created_at >= threshold)
            .group_by(LLMUsage.model)
            .all()
        )
        session.close()
    except Exception:
        logger.debug("Failed to read LLM usage from DB.", exc_info=True)
        return empty

    if not rows:
        return empty

    totals: dict[str, dict[str, int]] = {}
    calls = 0
    for model, inp, out, cnt in rows:
        totals[model] = {"input": inp, "output": out}
        calls += cnt

    total_input = sum(v["input"] for v in totals.values())
    total_output = sum(v["output"] for v in totals.values())
    total_cost = sum(
        estimate_cost(m, v["input"], v["output"]) for m, v in totals.items()
    )

    return {
        "models": totals,
        "total_input": total_input,
        "total_output": total_output,
        "total_cost": total_cost,
        "calls": calls,
    }
