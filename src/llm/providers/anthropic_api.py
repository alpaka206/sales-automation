"""
Anthropic API adapter. Used when ANTHROPIC_API_KEY is set and
LLM_PROVIDER=anthropic_api.

The `anthropic` package is an optional dependency; we import it lazily.
"""

from __future__ import annotations

import logging

from ...common.config import settings
from ..pricing import LLMResult

logger = logging.getLogger(__name__)


def call_anthropic(
    prompt: str,
    max_tokens: int = 2000,
    system: str | None = None,
) -> LLMResult:
    """Call Anthropic Messages API with optional cached system message."""
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "anthropic SDK not installed. `pip install anthropic` or "
            "switch LLM_PROVIDER to claude_cli."
        ) from e

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    kwargs: dict = {
        "model": settings.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }

    if system:
        kwargs["system"] = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    msg = client.messages.create(**kwargs)
    parts = [block.text for block in msg.content if getattr(block, "type", "") == "text"]
    text = "\n".join(parts).strip()

    usage = msg.usage
    return LLMResult(
        text=text,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        model=settings.ANTHROPIC_MODEL,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )
