"""Tests for outbound prospect enrichment via AI browser."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.agents.outbound.enrichment import enrich_prospect
from src.agents.outbound.sources.base import ProspectCandidate


def test_enrich_no_domain() -> None:
    candidate = ProspectCandidate(name="Test", source="manual_csv")
    assert enrich_prospect(candidate, MagicMock()) == {}


def test_enrich_success() -> None:
    candidate = ProspectCandidate(
        name="Test", domain="example.com", source="manual_csv"
    )
    mock_llm = MagicMock()

    with patch(
        "src.agents.outbound.enrichment.fetch_and_extract_sync",
        return_value="They sell SaaS tools for marketing teams.",
    ):
        result = enrich_prospect(candidate, mock_llm)

    assert result["homepage_summary"] == "They sell SaaS tools for marketing teams."
    assert result["enrichment_source"] == "ai_browser"


def test_enrich_ai_browser_returns_none() -> None:
    candidate = ProspectCandidate(
        name="Test", domain="slow.example.com", source="manual_csv"
    )
    mock_llm = MagicMock()

    with patch(
        "src.agents.outbound.enrichment.fetch_and_extract_sync",
        return_value=None,
    ):
        result = enrich_prospect(candidate, mock_llm)

    assert result == {}


def test_enrich_short_result_returns_empty() -> None:
    candidate = ProspectCandidate(
        name="Test", domain="empty.com", source="manual_csv"
    )
    mock_llm = MagicMock()

    with patch(
        "src.agents.outbound.enrichment.fetch_and_extract_sync",
        return_value="Short",
    ):
        result = enrich_prospect(candidate, mock_llm)

    assert result == {}


def test_enrich_exception_returns_empty() -> None:
    candidate = ProspectCandidate(
        name="Test", domain="broken.com", source="manual_csv"
    )
    mock_llm = MagicMock()

    with patch(
        "src.agents.outbound.enrichment.fetch_and_extract_sync",
        side_effect=RuntimeError("Connection failed"),
    ):
        result = enrich_prospect(candidate, mock_llm)

    assert result == {}
