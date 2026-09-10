"""Tests for LLM client — mocks the Gemini (Vertex) provider call."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import BaseModel

from src.common.config import settings
from src.llm.client import LLMClient, LLMError, _is_transient
from src.llm.pricing import LLMResult


@pytest.fixture()
def client() -> LLMClient:
    return LLMClient()


class _TestSchema(BaseModel):
    greeting: str


def _result(text: str) -> LLMResult:
    return LLMResult(text=text, input_tokens=10, output_tokens=5, model="gemini-2.5-flash")


@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.client.call_gemini")
def test_complete_text(mock_gemini, mock_log, client: LLMClient) -> None:
    mock_gemini.return_value = _result("Hello world")
    result = client.complete("test/hello", {"name": "Alice"})
    assert result == "Hello world"
    # company rules travel as the system instruction, not inlined in the prompt
    assert "system" in mock_gemini.call_args.kwargs


@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.client.call_gemini")
def test_complete_with_schema(mock_gemini, mock_log, client: LLMClient) -> None:
    mock_gemini.return_value = _result('{"greeting": "hi"}')
    result = client.complete("test/hello", {"name": "Bob"}, schema=_TestSchema)
    assert isinstance(result, _TestSchema)
    assert result.greeting == "hi"


@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.client.call_gemini")
def test_schema_retry_on_bad_json(mock_gemini, mock_log, client: LLMClient) -> None:
    mock_gemini.side_effect = [_result("not json"), _result('{"greeting": "fixed"}')]
    result = client.complete("test/hello", {"name": "C"}, schema=_TestSchema)
    assert result.greeting == "fixed"
    assert mock_gemini.call_count == 2


@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.client.call_gemini")
def test_schema_fails_twice_raises(mock_gemini, mock_log, client: LLMClient) -> None:
    mock_gemini.return_value = _result("not json at all")
    with pytest.raises(LLMError, match="invalid JSON twice"):
        client.complete("test/hello", {"name": "D"}, schema=_TestSchema)


@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.client.call_gemini")
def test_the_reply_rules_go_only_to_the_call_that_writes_a_reply(
    mock_gemini, mock_log, client: LLMClient
) -> None:
    """회신 규칙은 **초안 호출에만** 실리고, 그 회신의 단계로 실린다.

    예전에는 `complete()` 가 무조건 실었습니다 — 분류·라우팅·요약·번역·언어 판별·회사
    분석까지 규칙 블록(운영 55,296자)을 매 호출 지고 다녔고, 그중 어느 것도 회신 규칙이
    필요 없습니다. 이제 `stage` 를 준 호출만 받습니다.

    값이 아니라 **배선**을 봅니다: 로더를 갈아 끼우고 그 값이 그대로 도착하는지, 그리고
    단계가 로더까지 넘어가는지.
    """
    mock_gemini.return_value = _result("ok")
    seen: list = []

    def _rules(stage=None):
        seen.append(stage)
        return "RULES-FOR:" + str(stage)

    with patch("src.llm.client.get_company_rules", _rules):
        # 유틸리티 — 규칙을 아예 안 받는다. 로더를 부르지도 않는다.
        client.complete("test/hello", {"name": "F"})
        assert mock_gemini.call_args.kwargs["system"] == ""
        assert seen == []

        # 초안 — 그 회신의 단계로 받는다.
        client.complete("test/hello", {"name": "F"}, stage="followup")
        assert mock_gemini.call_args.kwargs["system"] == "RULES-FOR:followup"
        assert seen == ["followup"]


# ---- hybrid model tier tests ----


@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.client.call_gemini")
def test_default_tier_uses_flash_model(mock_gemini, mock_log, client: LLMClient) -> None:
    mock_gemini.return_value = _result("ok")
    client.complete("test/hello", {"name": "X"})
    assert mock_gemini.call_args.kwargs["model"] == settings.GEMINI_MODEL


@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.client.call_gemini")
def test_pro_tier_uses_pro_model(mock_gemini, mock_log, client: LLMClient) -> None:
    mock_gemini.return_value = _result("ok")
    client.complete("test/hello", {"name": "Y"}, tier="pro")
    assert mock_gemini.call_args.kwargs["model"] == settings.GEMINI_MODEL_PRO


@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.client.call_gemini")
def test_unknown_tier_falls_back_to_flash(mock_gemini, mock_log, client: LLMClient) -> None:
    mock_gemini.return_value = _result("ok")
    client.complete("test/hello", {"name": "Z"}, tier="nonsense")
    assert mock_gemini.call_args.kwargs["model"] == settings.GEMINI_MODEL


# ---- transient retry tests ----


def test_is_transient_5xx() -> None:
    err = Exception("server error")
    err.status_code = 500
    assert _is_transient(err) is True


def test_is_transient_429_via_code() -> None:
    err = Exception("rate limited")
    err.code = 429
    assert _is_transient(err) is True


def test_is_transient_4xx() -> None:
    err = Exception("bad request")
    err.status_code = 400
    assert _is_transient(err) is False


def test_is_transient_timeout_by_name() -> None:
    class ReadTimeout(Exception):
        pass

    assert _is_transient(ReadTimeout("deadline exceeded")) is True


def test_is_transient_unknown() -> None:
    assert _is_transient(ValueError("something else")) is False


@patch("src.llm.client.time.sleep")
@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.client.call_gemini")
def test_transient_error_retried_and_recovered(
    mock_gemini, mock_log, mock_sleep, client: LLMClient,
) -> None:
    transient = Exception("temporary 503")
    transient.code = 503
    mock_gemini.side_effect = [transient, _result("recovered output")]
    result = client.complete("test/hello", {"name": "G"})
    assert result == "recovered output"
    assert mock_gemini.call_count == 2
    mock_sleep.assert_called_once_with(2)


@patch("src.llm.client.time.sleep")
@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.client.call_gemini")
def test_transient_error_both_fail_raises(
    mock_gemini, mock_log, mock_sleep, client: LLMClient,
) -> None:
    transient = Exception("temporary 503")
    transient.code = 503
    mock_gemini.side_effect = transient
    with pytest.raises(Exception, match="503"):
        client.complete("test/hello", {"name": "H"})
    assert mock_gemini.call_count == 2


@patch("src.llm.client.time.sleep")
@patch("src.llm.client.LLMClient._log_event")
@patch("src.llm.client.call_gemini")
def test_permanent_error_not_retried(
    mock_gemini, mock_log, mock_sleep, client: LLMClient,
) -> None:
    mock_gemini.side_effect = ValueError("permanent config error")
    with pytest.raises(ValueError, match="permanent"):
        client.complete("test/hello", {"name": "I"})
    assert mock_gemini.call_count == 1
    mock_sleep.assert_not_called()
