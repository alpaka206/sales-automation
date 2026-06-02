"""Tests for AI browser harness with mocked Playwright and Gemini."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from pydantic import BaseModel

from src.integrations.ai_browser import (
    _clean_html,
    _extract_with_llm,
    _strip_fences,
    fetch_and_extract,
    fetch_and_extract_batch,
    fetch_and_extract_sync,
)
from src.llm.pricing import LLMResult

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class CompanyInfo(BaseModel):
    company_name: str
    email: str | None = None
    summary: str


def test_clean_html_removes_scripts() -> None:
    html = '<html><script>alert("x")</script><p>Hello</p><style>.a{}</style></html>'
    cleaned = _clean_html(html)
    assert "alert" not in cleaned
    assert ".a{}" not in cleaned
    assert "Hello" in cleaned


def test_clean_html_removes_ads() -> None:
    html = '<div class="ad-banner">Buy now!</div><p>Content</p>'
    cleaned = _clean_html(html)
    assert "Content" in cleaned


def test_clean_html_respects_max_chars() -> None:
    html = "<p>" + "x" * 50000 + "</p>"
    cleaned = _clean_html(html, max_chars=1000)
    assert len(cleaned) <= 1000


def test_strip_fences() -> None:
    assert _strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_fences('{"a": 1}') == '{"a": 1}'
    assert _strip_fences('```\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_with_llm_text_output() -> None:
    html = _load_fixture("company_page.html")
    mock_result = LLMResult(
        text="Acme Corp is an AI solutions company in Seoul.",
        input_tokens=100,
        output_tokens=20,
        model="gemini-2.5-flash",
    )

    with patch("src.integrations.ai_browser.call_gemini", return_value=mock_result):
        result = _extract_with_llm(
            html, "https://acmecorp.kr", "Summarize this company in one sentence.", None, 30000
        )

    assert "Acme Corp" in result


def test_extract_with_llm_schema_output() -> None:
    response_json = '{"company_name": "Acme Corp", "email": "hello@acmecorp.kr", "summary": "AI solutions"}'
    mock_result = LLMResult(
        text=response_json,
        input_tokens=100,
        output_tokens=30,
        model="gemini-2.5-flash",
    )

    html = _load_fixture("company_page.html")
    with patch("src.integrations.ai_browser.call_gemini", return_value=mock_result):
        result = _extract_with_llm(
            html,
            "https://acmecorp.kr",
            "Extract company info.",
            CompanyInfo,
            30000,
        )

    assert isinstance(result, CompanyInfo)
    assert result.company_name == "Acme Corp"
    assert result.email == "hello@acmecorp.kr"


def test_extract_with_llm_invalid_json_returns_none() -> None:
    mock_result = LLMResult(
        text="This is not JSON at all",
        input_tokens=100,
        output_tokens=10,
        model="gemini-2.5-flash",
    )

    with patch("src.integrations.ai_browser.call_gemini", return_value=mock_result):
        result = _extract_with_llm(
            "<p>test</p>",
            "https://test.com",
            "Extract info.",
            CompanyInfo,
            30000,
        )

    assert result is None


def test_extract_with_llm_cli_failure_returns_none() -> None:
    with patch(
        "src.integrations.ai_browser.call_gemini",
        side_effect=RuntimeError("CLI not found"),
    ):
        result = _extract_with_llm(
            "<p>test</p>", "https://test.com", "Extract info.", None, 30000
        )

    assert result is None


def test_fetch_and_extract_sync_httpx_fallback() -> None:
    import httpx
    import respx

    mock_result = LLMResult(
        text="Extracted data", input_tokens=50, output_tokens=10, model="gemini-2.5-flash"
    )

    with respx.mock:
        respx.get("https://test.com/page").mock(
            return_value=httpx.Response(
                200,
                text="<html><body><p>Test page</p></body></html>",
                headers={"content-type": "text/html"},
            )
        )

        with patch("src.integrations.ai_browser.call_gemini", return_value=mock_result):
            result = fetch_and_extract_sync(
                "https://test.com/page",
                "Summarize this page.",
            )

    assert result == "Extracted data"


def test_fetch_and_extract_async() -> None:
    import httpx
    import respx

    mock_result = LLMResult(
        text="Async result", input_tokens=50, output_tokens=10, model="gemini-2.5-flash"
    )

    with respx.mock:
        respx.get("https://async.test/").mock(
            return_value=httpx.Response(
                200,
                text="<html><body><p>Async page</p></body></html>",
                headers={"content-type": "text/html"},
            )
        )

        with patch("src.integrations.ai_browser.call_gemini", return_value=mock_result):
            result = asyncio.run(
                fetch_and_extract("https://async.test/", "Summarize.")
            )

    assert result == "Async result"


def test_fetch_and_extract_batch_async() -> None:
    import httpx
    import respx

    mock_result = LLMResult(
        text="Batch result", input_tokens=50, output_tokens=10, model="gemini-2.5-flash"
    )

    with respx.mock:
        for i in range(3):
            respx.get(f"https://batch{i}.test/").mock(
                return_value=httpx.Response(
                    200,
                    text=f"<html><body><p>Page {i}</p></body></html>",
                    headers={"content-type": "text/html"},
                )
            )

        with patch("src.integrations.ai_browser.call_gemini", return_value=mock_result):
            tasks = [{"url": f"https://batch{i}.test/"} for i in range(3)]
            results = asyncio.run(
                fetch_and_extract_batch(tasks, "Extract info.")
            )

    assert len(results) == 3
    assert all(r == "Batch result" for r in results)
