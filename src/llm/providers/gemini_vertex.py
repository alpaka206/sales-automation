"""
Google Gemini via Vertex AI — the only LLM provider.

Authenticates with a service-account JSON supplied through the
`GOOGLE_CREDENTIALS_JSON` env var (no API key). The GCP project is taken from
`GOOGLE_CLOUD_PROJECT`, or falls back to the `project_id` inside the JSON.

`google-genai` is a hard dependency; imports are kept inside functions so a
missing SDK surfaces as a clear runtime error rather than an import crash.
"""

from __future__ import annotations

import hashlib
import json
import logging

from ...common.config import settings
from ..pricing import LLMResult

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Cache built Vertex clients so we don't re-parse credentials and rebuild the
# genai.Client (a real auth/HTTP-session setup) on every single LLM call. Keyed
# on (project, location, creds-hash). Only successful builds are cached.
_client_cache: dict[tuple[str, str, str], object] = {}


class GeminiVertexError(RuntimeError):
    """Raised when Vertex credentials/config are missing or invalid."""


def _reset_client_cache() -> None:
    """Clear the cached Vertex client(s). Used by tests that inject a fake SDK."""
    _client_cache.clear()


def _build_client():
    """Create (or reuse a cached) google-genai Vertex client from the service-account JSON."""
    try:
        from google import genai
        from google.oauth2 import service_account
    except ImportError as e:  # pragma: no cover
        raise GeminiVertexError(
            "google-genai SDK not installed. Run `pip install google-genai`."
        ) from e

    raw = settings.GOOGLE_CREDENTIALS_JSON.strip()
    if not raw:
        raise GeminiVertexError(
            "GOOGLE_CREDENTIALS_JSON is empty — a Vertex service-account JSON is required."
        )
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as e:
        raise GeminiVertexError(f"GOOGLE_CREDENTIALS_JSON is not valid JSON: {e}") from e

    project = settings.GOOGLE_CLOUD_PROJECT or info.get("project_id")
    if not project:
        raise GeminiVertexError(
            "No GCP project — set GOOGLE_CLOUD_PROJECT or include project_id in the JSON."
        )
    location = settings.GOOGLE_CLOUD_LOCATION

    cache_key = (project, location, hashlib.sha256(raw.encode()).hexdigest())
    cached = _client_cache.get(cache_key)
    if cached is not None:
        return cached

    credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    client = genai.Client(
        vertexai=True,
        project=project,
        location=location,
        credentials=credentials,
    )
    _client_cache[cache_key] = client
    return client


def call_gemini(
    prompt: str,
    max_tokens: int = 2000,
    system: str | None = None,
    model: str | None = None,
    thinking_budget: int | None = None,
) -> LLMResult:
    """Generate content via Gemini on Vertex AI and return an LLMResult.

    ``model`` selects the Gemini model id; defaults to the flash tier
    (``settings.GEMINI_MODEL``). Callers pass the pro-tier id for
    quality-critical drafting.

    ``thinking_budget`` caps the "thinking" tokens of Gemini 2.5 models. This
    matters because thinking tokens are drawn from the SAME ``max_output_tokens``
    budget — left uncapped, a long internal reasoning trace can consume the whole
    budget and truncate (or empty) the actual answer, which then fails JSON
    parsing. Pass ``0`` to disable thinking (flash), a small int to bound it
    (pro has a hard minimum of 128), or ``None`` to leave the model default.
    """
    from google.genai import types

    model = model or settings.GEMINI_MODEL
    client = _build_client()
    config = types.GenerateContentConfig(
        max_output_tokens=max_tokens,
        system_instruction=system or None,
    )
    if thinking_budget is not None:
        # Guard against SDK variants that don't expose ThinkingConfig — a missing
        # cap is non-fatal, so degrade gracefully rather than crash the call.
        try:
            config.thinking_config = types.ThinkingConfig(thinking_budget=thinking_budget)
        except Exception:  # pragma: no cover - depends on SDK version
            logger.debug("ThinkingConfig unsupported by SDK; proceeding without a thinking cap.")
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=config,
    )

    text = (resp.text or "").strip()
    usage = getattr(resp, "usage_metadata", None)
    return LLMResult(
        text=text,
        input_tokens=getattr(usage, "prompt_token_count", None) or 0,
        output_tokens=getattr(usage, "candidates_token_count", None) or 0,
        model=model,
        cache_read_input_tokens=getattr(usage, "cached_content_token_count", None) or 0,
    )
