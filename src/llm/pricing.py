"""LLM token usage tracking and cost estimation."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4

PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-5-20250514": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    "llama3.1:8b": {"input": 0.0, "output": 0.0},
}

USAGE_FILE = os.path.join("data", "llm_usage.jsonl")


@dataclass
class LLMResult:
    """Wrapper returned by LLM providers."""

    text: str
    input_tokens: int
    output_tokens: int
    model: str


def estimate_tokens(text: str) -> int:
    """Rough token count from character length."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated cost in USD."""
    rates = PRICING.get(model, {"input": 3.00, "output": 15.00})
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def format_cost(amount: float) -> str:
    """Human-readable cost string."""
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


def log_usage(result: LLMResult, provider: str) -> None:
    """Append one usage record to the JSONL file."""
    try:
        os.makedirs(os.path.dirname(USAGE_FILE), exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            **asdict(result),
        }
        del record["text"]
        with open(USAGE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        logger.debug("Failed to log LLM usage.", exc_info=True)


def get_usage_since(since: datetime) -> dict:
    """Aggregate usage records from the JSONL file since a given time."""
    totals: dict[str, dict[str, int]] = {}
    if not os.path.exists(USAGE_FILE):
        return {"models": {}, "total_input": 0, "total_output": 0, "total_cost": 0.0, "calls": 0}

    calls = 0
    try:
        with open(USAGE_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                ts = datetime.fromisoformat(rec["ts"])
                threshold = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
                if ts < threshold:
                    continue
                model = rec.get("model", "unknown")
                if model not in totals:
                    totals[model] = {"input": 0, "output": 0}
                totals[model]["input"] += rec.get("input_tokens", 0)
                totals[model]["output"] += rec.get("output_tokens", 0)
                calls += 1
    except Exception:
        logger.debug("Failed to read LLM usage file.", exc_info=True)

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
