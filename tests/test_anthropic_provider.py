"""Tests for Anthropic API provider."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


def _mock_anthropic_module():
    """Create a fake anthropic module and inject into sys.modules."""
    mod = ModuleType("anthropic")
    mod.Anthropic = MagicMock()
    return mod


def _mock_response(text: str = "Hello", input_tokens: int = 10, output_tokens: int = 5,
                   cache_read: int = 3, cache_create: int = 2):
    """Create a mock Anthropic Messages response."""
    block = MagicMock()
    block.type = "text"
    block.text = text

    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    usage.cache_read_input_tokens = cache_read
    usage.cache_creation_input_tokens = cache_create

    msg = MagicMock()
    msg.content = [block]
    msg.usage = usage
    return msg


@pytest.fixture(autouse=True)
def _inject_anthropic():
    """Inject a mock anthropic module before each test, remove after."""
    fake_mod = _mock_anthropic_module()
    had_it = "anthropic" in sys.modules
    old_mod = sys.modules.get("anthropic")
    sys.modules["anthropic"] = fake_mod
    yield fake_mod
    if had_it:
        sys.modules["anthropic"] = old_mod
    else:
        sys.modules.pop("anthropic", None)
    # Reimport to avoid stale cache
    if "src.llm.providers.anthropic_api" in sys.modules:
        del sys.modules["src.llm.providers.anthropic_api"]


def test_call_anthropic_basic(_inject_anthropic) -> None:
    fake_mod = _inject_anthropic
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_response("Test response")
    fake_mod.Anthropic.return_value = mock_client

    with patch("src.llm.providers.anthropic_api.settings") as s:
        s.ANTHROPIC_API_KEY = "sk-test"
        s.ANTHROPIC_MODEL = "claude-sonnet-4-6"

        from src.llm.providers.anthropic_api import call_anthropic
        result = call_anthropic("Hello world")

    assert result.text == "Test response"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.cache_read_input_tokens == 3
    assert result.cache_creation_input_tokens == 2
    assert result.model == "claude-sonnet-4-6"


def test_call_anthropic_with_system(_inject_anthropic) -> None:
    fake_mod = _inject_anthropic
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_response("Sys response")
    fake_mod.Anthropic.return_value = mock_client

    with patch("src.llm.providers.anthropic_api.settings") as s:
        s.ANTHROPIC_API_KEY = "sk-test"
        s.ANTHROPIC_MODEL = "claude-sonnet-4-6"

        from src.llm.providers.anthropic_api import call_anthropic
        result = call_anthropic("Hello", system="You are helpful")

    assert result.text == "Sys response"
    call_kwargs = mock_client.messages.create.call_args
    system_arg = call_kwargs.kwargs.get("system")
    assert system_arg is not None
    assert system_arg[0]["text"] == "You are helpful"
    assert system_arg[0]["cache_control"] == {"type": "ephemeral"}


def test_call_anthropic_no_cache_attrs(_inject_anthropic) -> None:
    """Handles response where cache attributes don't exist."""
    fake_mod = _inject_anthropic

    block = MagicMock()
    block.type = "text"
    block.text = "Response"

    usage = MagicMock(spec=["input_tokens", "output_tokens"])
    usage.input_tokens = 10
    usage.output_tokens = 5

    msg = MagicMock()
    msg.content = [block]
    msg.usage = usage

    mock_client = MagicMock()
    mock_client.messages.create.return_value = msg
    fake_mod.Anthropic.return_value = mock_client

    with patch("src.llm.providers.anthropic_api.settings") as s:
        s.ANTHROPIC_API_KEY = "sk-test"
        s.ANTHROPIC_MODEL = "claude-sonnet-4-6"

        from src.llm.providers.anthropic_api import call_anthropic
        result = call_anthropic("Hi")

    assert result.cache_read_input_tokens == 0
    assert result.cache_creation_input_tokens == 0


def test_call_anthropic_multiple_text_blocks(_inject_anthropic) -> None:
    """Multiple text blocks are joined."""
    fake_mod = _inject_anthropic

    block1 = MagicMock(type="text", text="Part 1")
    block2 = MagicMock(type="text", text="Part 2")
    block3 = MagicMock()
    block3.type = "tool_use"

    usage = MagicMock()
    usage.input_tokens = 20
    usage.output_tokens = 10
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0

    msg = MagicMock()
    msg.content = [block1, block2, block3]
    msg.usage = usage

    mock_client = MagicMock()
    mock_client.messages.create.return_value = msg
    fake_mod.Anthropic.return_value = mock_client

    with patch("src.llm.providers.anthropic_api.settings") as s:
        s.ANTHROPIC_API_KEY = "sk-test"
        s.ANTHROPIC_MODEL = "claude-sonnet-4-6"

        from src.llm.providers.anthropic_api import call_anthropic
        result = call_anthropic("Multi")

    assert result.text == "Part 1\nPart 2"
