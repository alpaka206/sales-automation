"""
Google Gemini API adapter. Used when GEMINI_API_KEY is set and
LLM_PROVIDER=gemini_api (the default provider).

The `google-genai` package is an optional dependency; we import it lazily so
importing this module never fails when the SDK isn't installed.
"""

from __future__ import annotations

import logging

from ...common.config import settings
from ..pricing import LLMResult

logger = logging.getLogger(__name__)


def call_gemini(
    prompt: str,
    max_tokens: int = 2000,
    system: str | None = None,
) -> LLMResult:
    """Call the Gemini generate_content API with an optional system instruction."""
    try:
        from google import genai  # type: ignore[import-not-found]
        from google.genai import types  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "google-genai SDK not installed. `pip install google-genai` or "
            "switch LLM_PROVIDER to anthropic_api / claude_cli."
        ) from e

    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    config = types.GenerateContentConfig(
        max_output_tokens=max_tokens,
        system_instruction=system or None,
    )

    resp = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=prompt,
        config=config,
    )

    text = (resp.text or "").strip()

    usage = getattr(resp, "usage_metadata", None)
    input_tokens = getattr(usage, "prompt_token_count", None) or 0
    output_tokens = getattr(usage, "candidates_token_count", None) or 0
    cache_read = getattr(usage, "cached_content_token_count", None) or 0

    return LLMResult(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=settings.GEMINI_MODEL,
        cache_read_input_tokens=cache_read,
    )
