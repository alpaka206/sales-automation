"""Tests for LLM client — mocks subprocess.run for claude_cli provider."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from src.llm.client import LLMClient, LLMError, _is_transient
from src.llm.providers.claude_cli import ClaudeCLIError


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


# ---- transient retry tests ----


def test_is_transient_cli_timeout() -> None:
    assert _is_transient(ClaudeCLIError("claude CLI timed out after 180s")) is True


def test_is_transient_cli_nonzero_exit() -> None:
    assert _is_transient(ClaudeCLIError("claude CLI exited 1. stderr=...")) is True


def test_is_transient_cli_not_found() -> None:
    assert _is_transient(ClaudeCLIError("'claude' CLI not found on PATH")) is False


def test_is_transient_5xx() -> None:
    err = Exception("server error")
    err.status_code = 500
    assert _is_transient(err) is True


def test_is_transient_4xx() -> None:
    err = Exception("bad request")
    err.status_code = 400
    assert _is_transient(err) is False


def test_is_transient_unknown() -> None:
    assert _is_transient(ValueError("something else")) is False


@patch("src.llm.client.time.sleep")
@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.providers.claude_cli.subprocess.run")
def test_transient_cli_error_retried_and_recovered(
    mock_run, mock_log, mock_sleep, cli_client: LLMClient,
) -> None:
    mock_run.side_effect = [
        subprocess.TimeoutExpired(cmd="claude", timeout=180),
        _fake_subprocess("recovered output"),
    ]
    result = cli_client.complete("test/hello", {"name": "G"})
    assert result == "recovered output"
    assert mock_run.call_count == 2
    mock_sleep.assert_called_once_with(2)


@patch("src.llm.client.time.sleep")
@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.providers.claude_cli.subprocess.run")
def test_transient_cli_error_both_fail_raises(
    mock_run, mock_log, mock_sleep, cli_client: LLMClient,
) -> None:
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=180)
    with pytest.raises(ClaudeCLIError, match="timed out"):
        cli_client.complete("test/hello", {"name": "H"})
    assert mock_run.call_count == 2


def test_permanent_error_not_retried() -> None:
    client = LLMClient(provider="nonexistent")
    with pytest.raises(LLMError, match="unknown LLM_PROVIDER"):
        client.complete("test/hello", {"name": "I"})


@patch("src.llm.client.time.sleep")
@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.providers.claude_cli.subprocess.run")
def test_permanent_cli_not_found_not_retried(
    mock_run, mock_log, mock_sleep, cli_client: LLMClient,
) -> None:
    mock_run.side_effect = FileNotFoundError("No such file: claude")
    with pytest.raises(ClaudeCLIError, match="not found"):
        cli_client.complete("test/hello", {"name": "J"})
    assert mock_run.call_count == 1
    mock_sleep.assert_not_called()
