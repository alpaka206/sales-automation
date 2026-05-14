"""Tests for outbound prospect enrichment."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from src.agents.outbound.enrichment import _strip_html, enrich_prospect
from src.agents.outbound.sources.base import ProspectCandidate


def test_strip_html_removes_tags() -> None:
    html = "<html><body><h1>Hello</h1><p>World</p></body></html>"
    assert "Hello" in _strip_html(html)
    assert "World" in _strip_html(html)
    assert "<" not in _strip_html(html)


def test_strip_html_removes_scripts_and_styles() -> None:
    html = "<style>body{color:red}</style><script>alert(1)</script><p>Visible</p>"
    result = _strip_html(html)
    assert "Visible" in result
    assert "alert" not in result
    assert "color" not in result


def test_strip_html_truncates() -> None:
    html = "<p>" + "x" * 5000 + "</p>"
    assert len(_strip_html(html)) <= 3000


def test_enrich_no_domain() -> None:
    candidate = ProspectCandidate(name="Test", source="manual_csv")
    assert enrich_prospect(candidate, MagicMock()) == {}


def test_enrich_success() -> None:
    candidate = ProspectCandidate(
        name="Test", domain="example.com", source="manual_csv"
    )
    mock_llm = MagicMock()
    mock_llm.complete.return_value = "They sell SaaS tools for marketing teams."

    fake_resp = httpx.Response(
        200,
        text="<html><body><h1>Example Corp</h1><p>We build marketing automation software for small and medium businesses worldwide.</p></body></html>",
        request=httpx.Request("GET", "https://example.com"),
    )

    with patch("src.agents.outbound.enrichment.httpx.Client") as MockClient:
        mock_cx = MagicMock()
        mock_cx.__enter__ = MagicMock(return_value=mock_cx)
        mock_cx.__exit__ = MagicMock(return_value=False)
        mock_cx.get.return_value = fake_resp
        MockClient.return_value = mock_cx

        result = enrich_prospect(candidate, mock_llm)

    assert result["homepage_summary"] == "They sell SaaS tools for marketing teams."
    assert result["enrichment_source"] == "homepage"
    mock_llm.complete.assert_called_once()
    call_args = mock_llm.complete.call_args[0]
    assert call_args[0] == "outbound/enrich_homepage"


def test_enrich_timeout_returns_empty() -> None:
    candidate = ProspectCandidate(
        name="Test", domain="slow.example.com", source="manual_csv"
    )
    mock_llm = MagicMock()

    with patch("src.agents.outbound.enrichment.httpx.Client") as MockClient:
        mock_cx = MagicMock()
        mock_cx.__enter__ = MagicMock(return_value=mock_cx)
        mock_cx.__exit__ = MagicMock(return_value=False)
        mock_cx.get.side_effect = httpx.TimeoutException("timed out")
        MockClient.return_value = mock_cx

        result = enrich_prospect(candidate, mock_llm)

    assert result == {}
    mock_llm.complete.assert_not_called()


def test_enrich_http_error_returns_empty() -> None:
    candidate = ProspectCandidate(
        name="Test", domain="broken.example.com", source="manual_csv"
    )
    mock_llm = MagicMock()

    fake_resp = httpx.Response(
        500,
        text="Server Error",
        request=httpx.Request("GET", "https://broken.example.com"),
    )

    with patch("src.agents.outbound.enrichment.httpx.Client") as MockClient:
        mock_cx = MagicMock()
        mock_cx.__enter__ = MagicMock(return_value=mock_cx)
        mock_cx.__exit__ = MagicMock(return_value=False)
        mock_cx.get.return_value = fake_resp
        MockClient.return_value = mock_cx

        result = enrich_prospect(candidate, mock_llm)

    assert result == {}


def test_enrich_short_text_returns_empty() -> None:
    candidate = ProspectCandidate(
        name="Test", domain="empty.com", source="manual_csv"
    )
    mock_llm = MagicMock()

    fake_resp = httpx.Response(
        200,
        text="<html><body></body></html>",
        request=httpx.Request("GET", "https://empty.com"),
    )

    with patch("src.agents.outbound.enrichment.httpx.Client") as MockClient:
        mock_cx = MagicMock()
        mock_cx.__enter__ = MagicMock(return_value=mock_cx)
        mock_cx.__exit__ = MagicMock(return_value=False)
        mock_cx.get.return_value = fake_resp
        MockClient.return_value = mock_cx

        result = enrich_prospect(candidate, mock_llm)

    assert result == {}
    mock_llm.complete.assert_not_called()
