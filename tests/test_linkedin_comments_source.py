"""Tests for LinkedIn comments source."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents.outbound.source_registry import get_source
from src.agents.outbound.sources.linkedin_comments import (
    LinkedInCommentsSource,
    _extract_company_from_headline,
    _extract_post_id,
    _to_candidate,
)


def test_registry_returns_linkedin_comments() -> None:
    source = get_source("linkedin_comments")
    assert source.name == "linkedin_comments"


def test_discover_raises_when_disabled() -> None:
    source = LinkedInCommentsSource()
    with patch("src.agents.outbound.sources.linkedin_comments.settings") as mock_settings:
        mock_settings.LINKEDIN_SCRAPING_ENABLED = False
        with pytest.raises(NotImplementedError, match="disabled"):
            source.discover({"post_urls": ["https://linkedin.com/posts/example-123"]})


def test_discover_requires_post_urls() -> None:
    source = LinkedInCommentsSource()
    with patch("src.agents.outbound.sources.linkedin_comments.settings") as mock_settings:
        mock_settings.LINKEDIN_SCRAPING_ENABLED = True
        mock_settings.LINKEDIN_API_TOKEN = ""
        mock_settings.LINKEDIN_SESSION_COOKIE = "abc"
        with pytest.raises(ValueError, match="post_urls"):
            source.discover({})


def test_extract_post_id() -> None:
    assert _extract_post_id("https://www.linkedin.com/posts/activity-7123456789") == "7123456789"
    assert _extract_post_id("https://linkedin.com/feed/update/urn:li:activity:12345") == "12345"
    assert _extract_post_id("https://linkedin.com/posts/john-doe-12345") == "12345"
    assert _extract_post_id("https://example.com/nothing") is None


def test_extract_company_from_headline() -> None:
    assert _extract_company_from_headline("CTO at Acme Corp") == "Acme Corp"
    assert _extract_company_from_headline("Engineer @ Startup") == "Startup"
    assert _extract_company_from_headline("PM | BigCo") == "BigCo"
    assert _extract_company_from_headline("Just a person") is None


def test_to_candidate() -> None:
    commenter = {
        "name": "Jane Doe",
        "profile_url": "https://linkedin.com/in/janedoe",
        "headline": "VP Marketing at TechCorp",
        "comment_excerpt": "Great insight!",
    }
    c = _to_candidate(commenter, "https://linkedin.com/posts/test-123")
    assert c.name == "Jane Doe"
    assert c.company == "TechCorp"
    assert c.role == "VP Marketing at TechCorp"
    assert c.source == "linkedin_comments"
    assert c.source_ref == "https://linkedin.com/posts/test-123"
    assert c.extra["profile_url"] == "https://linkedin.com/in/janedoe"
    assert c.extra["comment_excerpt"] == "Great insight!"


def test_discover_api_backend() -> None:
    source = LinkedInCommentsSource()

    fake_commenters = [
        {
            "name": "Alice Kim",
            "profile_url": "https://linkedin.com/in/alicekim",
            "headline": "CEO at SaaSCo",
            "comment_excerpt": "Interesting post",
        },
        {
            "name": "Bob Park",
            "profile_url": "https://linkedin.com/in/bobpark",
            "headline": "Engineer @ DevShop",
            "comment_excerpt": "Agree!",
        },
    ]

    with (
        patch("src.agents.outbound.sources.linkedin_comments.settings") as mock_settings,
        patch("src.agents.outbound.sources.linkedin_comments._fetch_commenters_api") as mock_fetch,
    ):
        mock_settings.LINKEDIN_SCRAPING_ENABLED = True
        mock_settings.LINKEDIN_API_TOKEN = "token123"
        mock_fetch.return_value = fake_commenters

        results = source.discover({
            "post_urls": ["https://linkedin.com/posts/activity-999"],
        })

    assert len(results) == 2
    assert results[0].name == "Alice Kim"
    assert results[0].company == "SaaSCo"
    assert results[1].name == "Bob Park"
    mock_fetch.assert_called_once_with("999", 50)


def test_discover_with_country_filter() -> None:
    source = LinkedInCommentsSource()

    fake_commenters = [
        {"name": "A", "profile_url": "", "headline": "CEO at X", "comment_excerpt": ""},
    ]

    with (
        patch("src.agents.outbound.sources.linkedin_comments.settings") as mock_settings,
        patch("src.agents.outbound.sources.linkedin_comments._fetch_commenters_api") as mock_fetch,
    ):
        mock_settings.LINKEDIN_SCRAPING_ENABLED = True
        mock_settings.LINKEDIN_API_TOKEN = "token123"
        mock_fetch.return_value = fake_commenters

        results = source.discover({
            "post_urls": ["https://linkedin.com/posts/activity-999"],
            "countries": ["KR"],
        })

    assert len(results) == 0


def test_api_failure_continues_to_next_post() -> None:
    source = LinkedInCommentsSource()

    with (
        patch("src.agents.outbound.sources.linkedin_comments.settings") as mock_settings,
        patch("src.agents.outbound.sources.linkedin_comments._fetch_commenters_api") as mock_fetch,
    ):
        mock_settings.LINKEDIN_SCRAPING_ENABLED = True
        mock_settings.LINKEDIN_API_TOKEN = "token123"
        mock_fetch.side_effect = [
            RuntimeError("network error"),
            [{"name": "OK Person", "profile_url": "", "headline": "", "comment_excerpt": ""}],
        ]

        results = source.discover({
            "post_urls": [
                "https://linkedin.com/posts/activity-111",
                "https://linkedin.com/posts/activity-222",
            ],
        })

    assert len(results) == 1
    assert results[0].name == "OK Person"
