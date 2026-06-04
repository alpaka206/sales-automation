"""Tests for the Gemini (Vertex AI) provider — injects a fake google-genai SDK."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from src.llm.providers.gemini_vertex import (
    GeminiVertexError,
    _reset_client_cache,
    call_gemini,
)

_CREDS_JSON = '{"type": "service_account", "project_id": "json-proj"}'


def _mock_response(text="Hello", prompt_tokens=10, candidate_tokens=5, cached_tokens=3):
    usage = MagicMock()
    usage.prompt_token_count = prompt_tokens
    usage.candidates_token_count = candidate_tokens
    usage.cached_content_token_count = cached_tokens
    resp = MagicMock()
    resp.text = text
    resp.usage_metadata = usage
    return resp


@pytest.fixture
def fake_google():
    """Inject fake google / google.genai / google.oauth2 modules."""
    keys = [
        "google", "google.genai", "google.genai.types",
        "google.oauth2", "google.oauth2.service_account",
    ]
    saved = {k: sys.modules.get(k) for k in keys}

    google_mod = ModuleType("google")
    genai_mod = ModuleType("google.genai")
    types_mod = ModuleType("google.genai.types")
    oauth2_mod = ModuleType("google.oauth2")
    sa_mod = ModuleType("google.oauth2.service_account")

    google_mod.genai = genai_mod
    google_mod.oauth2 = oauth2_mod
    genai_mod.types = types_mod
    genai_mod.Client = MagicMock()
    types_mod.GenerateContentConfig = MagicMock()
    oauth2_mod.service_account = sa_mod
    sa_mod.Credentials = MagicMock()
    sa_mod.Credentials.from_service_account_info = MagicMock(return_value=MagicMock())

    sys.modules.update({
        "google": google_mod,
        "google.genai": genai_mod,
        "google.genai.types": types_mod,
        "google.oauth2": oauth2_mod,
        "google.oauth2.service_account": sa_mod,
    })
    # The provider caches built clients; clear it so each test gets a fresh build
    # against this test's freshly-injected fake SDK.
    _reset_client_cache()
    yield genai_mod
    _reset_client_cache()
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def test_call_gemini_basic(fake_google) -> None:
    client = MagicMock()
    client.models.generate_content.return_value = _mock_response("Test response")
    fake_google.Client.return_value = client

    with patch("src.llm.providers.gemini_vertex.settings") as s:
        s.GOOGLE_CREDENTIALS_JSON = _CREDS_JSON
        s.GOOGLE_CLOUD_PROJECT = ""
        s.GOOGLE_CLOUD_LOCATION = "global"
        s.GEMINI_MODEL = "gemini-2.5-flash"
        result = call_gemini("Hello world")

    assert result.text == "Test response"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.cache_read_input_tokens == 3
    assert result.model == "gemini-2.5-flash"


def test_call_gemini_uses_vertex_and_json_project(fake_google) -> None:
    client = MagicMock()
    client.models.generate_content.return_value = _mock_response("ok")
    fake_google.Client.return_value = client

    with patch("src.llm.providers.gemini_vertex.settings") as s:
        s.GOOGLE_CREDENTIALS_JSON = _CREDS_JSON
        s.GOOGLE_CLOUD_PROJECT = ""        # fall back to project_id in the JSON
        s.GOOGLE_CLOUD_LOCATION = "us-central1"
        s.GEMINI_MODEL = "gemini-2.5-flash"
        call_gemini("Hello", system="You are helpful", max_tokens=512)

    client_kwargs = fake_google.Client.call_args.kwargs
    assert client_kwargs["vertexai"] is True
    assert client_kwargs["project"] == "json-proj"
    assert client_kwargs["location"] == "us-central1"


def test_call_gemini_empty_creds_raises(fake_google) -> None:
    with patch("src.llm.providers.gemini_vertex.settings") as s:
        s.GOOGLE_CREDENTIALS_JSON = ""
        with pytest.raises(GeminiVertexError, match="GOOGLE_CREDENTIALS_JSON"):
            call_gemini("Hi")


def test_call_gemini_none_text(fake_google) -> None:
    resp = _mock_response(candidate_tokens=0)
    resp.text = None
    client = MagicMock()
    client.models.generate_content.return_value = resp
    fake_google.Client.return_value = client

    with patch("src.llm.providers.gemini_vertex.settings") as s:
        s.GOOGLE_CREDENTIALS_JSON = _CREDS_JSON
        s.GOOGLE_CLOUD_PROJECT = ""
        s.GOOGLE_CLOUD_LOCATION = "global"
        s.GEMINI_MODEL = "gemini-2.5-flash"
        result = call_gemini("Hi")

    assert result.text == ""
