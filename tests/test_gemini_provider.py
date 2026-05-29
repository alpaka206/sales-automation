"""Tests for the Gemini API provider — injects a fake google-genai SDK."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


def _mock_response(text: str = "Hello", prompt_tokens: int = 10, candidate_tokens: int = 5,
                   cached_tokens: int = 3):
    """Build a fake Gemini generate_content response."""
    usage = MagicMock()
    usage.prompt_token_count = prompt_tokens
    usage.candidates_token_count = candidate_tokens
    usage.cached_content_token_count = cached_tokens

    resp = MagicMock()
    resp.text = text
    resp.usage_metadata = usage
    return resp


@pytest.fixture
def fake_genai():
    """Inject fake `google`, `google.genai`, `google.genai.types` modules."""
    saved = {k: sys.modules.get(k) for k in ("google", "google.genai", "google.genai.types")}

    google_mod = ModuleType("google")
    genai_mod = ModuleType("google.genai")
    types_mod = ModuleType("google.genai.types")
    google_mod.genai = genai_mod
    genai_mod.types = types_mod
    genai_mod.Client = MagicMock()
    types_mod.GenerateContentConfig = MagicMock()

    sys.modules["google"] = google_mod
    sys.modules["google.genai"] = genai_mod
    sys.modules["google.genai.types"] = types_mod
    sys.modules.pop("src.llm.providers.gemini_api", None)

    yield genai_mod

    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
    sys.modules.pop("src.llm.providers.gemini_api", None)


def test_call_gemini_basic(fake_genai) -> None:
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _mock_response("Test response")
    fake_genai.Client.return_value = mock_client

    with patch("src.llm.providers.gemini_api.settings") as s:
        s.GEMINI_API_KEY = "g-test"
        s.GEMINI_MODEL = "gemini-2.5-flash"

        from src.llm.providers.gemini_api import call_gemini
        result = call_gemini("Hello world")

    assert result.text == "Test response"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.cache_read_input_tokens == 3
    assert result.model == "gemini-2.5-flash"


def test_call_gemini_with_system(fake_genai) -> None:
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _mock_response("Sys response")
    fake_genai.Client.return_value = mock_client

    with patch("src.llm.providers.gemini_api.settings") as s:
        s.GEMINI_API_KEY = "g-test"
        s.GEMINI_MODEL = "gemini-2.5-flash"

        from src.llm.providers.gemini_api import call_gemini
        result = call_gemini("Hello", system="You are helpful", max_tokens=512)

    assert result.text == "Sys response"
    call = mock_client.models.generate_content.call_args
    assert call.kwargs["model"] == "gemini-2.5-flash"
    assert call.kwargs["contents"] == "Hello"
    # The config is built from the fake GenerateContentConfig with our kwargs.
    fake_genai.types.GenerateContentConfig.assert_called_once()
    cfg_kwargs = fake_genai.types.GenerateContentConfig.call_args.kwargs
    assert cfg_kwargs["system_instruction"] == "You are helpful"
    assert cfg_kwargs["max_output_tokens"] == 512


def test_call_gemini_none_text(fake_genai) -> None:
    """A blocked/empty response (text=None) yields an empty string, not a crash."""
    resp = _mock_response(candidate_tokens=0)
    resp.text = None
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = resp
    fake_genai.Client.return_value = mock_client

    with patch("src.llm.providers.gemini_api.settings") as s:
        s.GEMINI_API_KEY = "g-test"
        s.GEMINI_MODEL = "gemini-2.5-flash"

        from src.llm.providers.gemini_api import call_gemini
        result = call_gemini("Hi")

    assert result.text == ""
