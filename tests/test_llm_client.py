"""Tests for LLM client — mocks subprocess.run for claude_cli provider."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from src.llm.client import LLMClient, LLMError


@pytest.fixture()
def cli_client() -> LLMClient:
    return LLMClient(provider="claude_cli")


class _TestSchema(BaseModel):
    greeting: str


def _fake_subprocess(stdout: str = "Hello world", returncode: int = 0):
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
    return result


@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.providers.claude_cli.subprocess.run")
def test_complete_text(mock_run, mock_log, cli_client: LLMClient) -> None:
    mock_run.return_value = _fake_subprocess("Hello world")
    result = cli_client.complete("test/hello", {"name": "Alice"})
    assert result == "Hello world"

    args = mock_run.call_args
    cmd = args[0][0]
    assert cmd[0].endswith("claude") or cmd[0] == "claude"
    assert "-p" in cmd
    assert "--output-format" in cmd
    assert "text" in cmd


@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.providers.claude_cli.subprocess.run")
def test_complete_with_schema(mock_run, mock_log, cli_client: LLMClient) -> None:
    mock_run.return_value = _fake_subprocess('{"greeting": "hi"}')
    result = cli_client.complete("test/hello", {"name": "Bob"}, schema=_TestSchema)
    assert isinstance(result, _TestSchema)
    assert result.greeting == "hi"


@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.providers.claude_cli.subprocess.run")
def test_schema_retry_on_bad_json(mock_run, mock_log, cli_client: LLMClient) -> None:
    mock_run.side_effect = [
        _fake_subprocess("not json"),
        _fake_subprocess('{"greeting": "fixed"}'),
    ]
    result = cli_client.complete("test/hello", {"name": "C"}, schema=_TestSchema)
    assert result.greeting == "fixed"
    assert mock_run.call_count == 2


@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.providers.claude_cli.subprocess.run")
def test_schema_fails_twice_raises(mock_run, mock_log, cli_client: LLMClient) -> None:
    mock_run.return_value = _fake_subprocess("not json at all")
    with pytest.raises(LLMError, match="invalid JSON twice"):
        cli_client.complete("test/hello", {"name": "D"}, schema=_TestSchema)


def test_unknown_provider_raises() -> None:
    client = LLMClient(provider="nonexistent")
    with pytest.raises(LLMError, match="unknown LLM_PROVIDER"):
        client.complete("test/hello", {"name": "E"})


@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.providers.claude_cli.subprocess.run")
def test_prompt_includes_company_rules(mock_run, mock_log, cli_client: LLMClient) -> None:
    mock_run.return_value = _fake_subprocess("ok")
    cli_client.complete("test/hello", {"name": "F"})
    prompt_sent = mock_run.call_args[0][0][2]
    assert "Company rules" in prompt_sent
