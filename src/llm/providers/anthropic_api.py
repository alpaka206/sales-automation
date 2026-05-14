"""
Anthropic API adapter. Used when ANTHROPIC_API_KEY is set and
LLM_PROVIDER=anthropic_api.

The `anthropic` package is an optional dependency; we import it lazily.
"""

from __future__ import annotations

import logging

from ...common.config import settings

logger = logging.getLogger(__name__)


def call_anthropic(prompt: str, max_tokens: int = 2000) -> str:
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "anthropic SDK not installed. `pip install anthropic` or "
            "switch LLM_PROVIDER to claude_cli."
        ) from e

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [block.text for block in msg.content if getattr(block, "type", "") == "text"]
    return "\n".join(parts).strip()
