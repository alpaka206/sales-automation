"""
Google Gemini via Vertex AI — the only LLM provider.

Authenticates with a service-account JSON supplied through the
`GOOGLE_CREDENTIALS_JSON` env var (no API key). The GCP project is taken from
`GOOGLE_CLOUD_PROJECT`, or falls back to the `project_id` inside the JSON.

`google-genai` is a hard dependency; imports are kept inside functions so a
missing SDK surfaces as a clear runtime error rather than an import crash.
"""

from __future__ import annotations

import json
import logging

from ...common.config import settings
from ..pricing import LLMResult

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class GeminiVertexError(RuntimeError):
    """Raised when Vertex credentials/config are missing or invalid."""


def _build_client():
    """Create a google-genai Vertex client from the service-account JSON env var."""
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

    credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    project = settings.GOOGLE_CLOUD_PROJECT or info.get("project_id")
    if not project:
        raise GeminiVertexError(
            "No GCP project — set GOOGLE_CLOUD_PROJECT or include project_id in the JSON."
        )

    return genai.Client(
        vertexai=True,
        project=project,
        location=settings.GOOGLE_CLOUD_LOCATION,
        credentials=credentials,
    )


def call_gemini(
    prompt: str,
    max_tokens: int = 2000,
    system: str | None = None,
    model: str | None = None,
) -> LLMResult:
    """Generate content via Gemini on Vertex AI and return an LLMResult.

    ``model`` selects the Gemini model id; defaults to the flash tier
    (``settings.GEMINI_MODEL``). Callers pass the pro-tier id for
    quality-critical drafting.
    """
    from google.genai import types

    model = model or settings.GEMINI_MODEL
    client = _build_client()
    config = types.GenerateContentConfig(
        max_output_tokens=max_tokens,
        system_instruction=system or None,
    )
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
