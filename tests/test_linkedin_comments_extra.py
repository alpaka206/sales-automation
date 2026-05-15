"""Additional tests for LinkedIn comments source — enrich_emails and playwright paths."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents.outbound.sources.linkedin_comments import (
    LinkedInCommentsSource,
    _fetch_commenters_api,
    _to_candidate,
)
from src.agents.outbound.sources.base import ProspectCandidate


# ---------- _enrich_emails (covers lines 52-73) ----------


def test_enrich_emails_fetches_for_missing() -> None:
    source = LinkedInCommentsSource()
    prospects = [
        ProspectCandidate(
            name="No Email",
            source="linkedin_comments",
            extra={"profile_url": "https://linkedin.com/in/test"},
        ),
        ProspectCandidate(
            name="Has Email",
            email="exists@co.com",
            source="linkedin_comments",
            extra={"profile_url": "https://linkedin.com/in/test2"},
        ),
    ]

    with patch(
        "src.agents.outbound.sources.linkedin_comments.fetch_profile_email",
        return_value="found@co.com",
    ) as mock_fetch, patch(
        "src.agents.outbound.sources.linkedin_comments.settings"
    ) as s:
        s.LINKEDIN_SESSION_COOKIE = "cookie123"
        source._enrich_emails(prospects)

    mock_fetch.assert_called_once_with(
        "https://linkedin.com/in/test", "cookie123"
    )
    assert prospects[0].email == "found@co.com"


def test_enrich_emails_skips_when_no_candidates() -> None:
    source = LinkedInCommentsSource()
    prospects = [
        ProspectCandidate(
            name="Has Email",
            email="x@y.com",
            source="linkedin_comments",
            extra={},
        ),
    ]

    with patch(
        "src.agents.outbound.sources.linkedin_comments.fetch_profile_email"
    ) as mock_fetch:
        source._enrich_emails(prospects)

    mock_fetch.assert_not_called()


def test_enrich_emails_handles_exception() -> None:
    source = LinkedInCommentsSource()
    prospects = [
        ProspectCandidate(
            name="Fail",
            source="linkedin_comments",
            extra={"profile_url": "https://linkedin.com/in/fail"},
        ),
    ]

    with patch(
        "src.agents.outbound.sources.linkedin_comments.fetch_profile_email",
        side_effect=RuntimeError("network"),
    ), patch(
        "src.agents.outbound.sources.linkedin_comments.settings"
    ) as s:
        s.LINKEDIN_SESSION_COOKIE = "cookie"
        source._enrich_emails(prospects)

    assert prospects[0].email is None


# ---------- discover with session cookie triggers enrich (covers line 45-46) ----------


def test_discover_api_with_session_cookie_enriches() -> None:
    source = LinkedInCommentsSource()

    fake_commenters = [
        {
            "name": "Alice",
            "profile_url": "https://linkedin.com/in/alice",
            "headline": "PM at Co",
            "comment_excerpt": "Nice",
        },
    ]

    with patch(
        "src.agents.outbound.sources.linkedin_comments.settings"
    ) as s, patch(
        "src.agents.outbound.sources.linkedin_comments._fetch_commenters_api",
        return_value=fake_commenters,
    ), patch(
        "src.agents.outbound.sources.linkedin_comments.fetch_profile_email",
        return_value="alice@co.com",
    ):
        s.LINKEDIN_SCRAPING_ENABLED = True
        s.LINKEDIN_API_TOKEN = "token"
        s.LINKEDIN_SESSION_COOKIE = "cookie"

        results = source.discover(
            {"post_urls": ["https://linkedin.com/posts/activity-999"]}
        )

    assert len(results) == 1
    assert results[0].email == "alice@co.com"


# ---------- _discover_api with invalid URL (covers line 83-84) ----------


def test_discover_api_skips_invalid_url() -> None:
    source = LinkedInCommentsSource()

    with patch(
        "src.agents.outbound.sources.linkedin_comments.settings"
    ) as s, patch(
        "src.agents.outbound.sources.linkedin_comments._fetch_commenters_api"
    ) as mock_fetch:
        s.LINKEDIN_SCRAPING_ENABLED = True
        s.LINKEDIN_API_TOKEN = "token"
        s.LINKEDIN_SESSION_COOKIE = ""

        results = source.discover(
            {"post_urls": ["https://example.com/no-post-id-here"]}
        )

    mock_fetch.assert_not_called()
    assert results == []


# ---------- _discover_playwright no cookie (covers line 99-103) ----------


def test_discover_playwright_requires_cookie() -> None:
    source = LinkedInCommentsSource()

    with patch(
        "src.agents.outbound.sources.linkedin_comments.settings"
    ) as s:
        s.LINKEDIN_SCRAPING_ENABLED = True
        s.LINKEDIN_API_TOKEN = ""
        s.LINKEDIN_SESSION_COOKIE = ""

        with pytest.raises(ValueError, match="LINKEDIN_SESSION_COOKIE"):
            source.discover(
                {"post_urls": ["https://linkedin.com/posts/activity-999"]}
            )


# ---------- _fetch_commenters_api (covers lines 136-159) ----------


def test_fetch_commenters_api() -> None:
    import httpx
    import respx

    with respx.mock:
        respx.get(
            "https://api.linkedin.com/v2/socialActions/urn:li:activity:12345/comments"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "actor~": {
                                "localizedFirstName": "Jane",
                                "localizedLastName": "Doe",
                                "vanityName": "janedoe",
                                "localizedHeadline": "CTO at Startup",
                            },
                            "message": {"text": "Great insight!"},
                        },
                        {
                            "actor": "urn:li:person:abc",
                        },
                    ]
                },
            )
        )

        with patch(
            "src.agents.outbound.sources.linkedin_comments.settings"
        ) as s:
            s.LINKEDIN_API_TOKEN = "token123"
            commenters = _fetch_commenters_api("12345", 10)

    assert len(commenters) == 1
    assert commenters[0]["name"] == "Jane Doe"
    assert "janedoe" in commenters[0]["profile_url"]
    assert commenters[0]["headline"] == "CTO at Startup"
    assert commenters[0]["comment_excerpt"] == "Great insight!"
