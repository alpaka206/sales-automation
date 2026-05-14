"""Tests for YouTube source with mocked API."""

from __future__ import annotations

import httpx
import pytest
import respx

from src.agents.outbound.sources.youtube import YouTubeSource, _extract_email
from src.integrations.youtube import BASE_URL, YouTubeClient


def test_extract_email() -> None:
    assert _extract_email("Contact us at hello@studio.kr for collabs") == "hello@studio.kr"
    assert _extract_email("No email here") is None
    assert _extract_email("two@a.com and three@b.com") == "two@a.com"


@respx.mock
def test_youtube_source_discover() -> None:
    respx.get(f"{BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": {"channelId": "UC123"}},
                    {"id": {"channelId": "UC456"}},
                ]
            },
        )
    )

    respx.get(f"{BASE_URL}/channels").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "snippet": {
                                "title": "Tech Channel",
                                "description": "Business: tech@example.kr\nGreat content.",
                                "country": "KR",
                            },
                            "statistics": {"subscriberCount": "50000"},
                        }
                    ]
                },
            ),
            httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "snippet": {
                                "title": "Small Channel",
                                "description": "No contact info.",
                                "country": "KR",
                            },
                            "statistics": {"subscriberCount": "100"},
                        }
                    ]
                },
            ),
        ]
    )

    client = YouTubeClient(api_key="test-key")
    source = YouTubeSource(client=client)
    results = source.discover({"query": "tech", "min_subscribers": 1000})

    assert len(results) == 1
    assert results[0].name == "Tech Channel"
    assert results[0].email == "tech@example.kr"
    assert results[0].source == "youtube"
    assert results[0].extra["subscribers"] == 50000


def test_youtube_source_no_key() -> None:
    source = YouTubeSource(client=None)
    source.client = None
    results = source.discover({"query": "test"})
    assert results == []


def test_youtube_source_requires_query() -> None:
    client = YouTubeClient(api_key="test")
    source = YouTubeSource(client=client)
    with pytest.raises(ValueError, match="query"):
        source.discover({})
