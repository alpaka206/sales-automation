"""Tests for Google Custom Search source with mocked API."""

from __future__ import annotations


import httpx
import pytest
import respx

from src.agents.outbound.sources.google_search import (
    GoogleSearchSource,
    _extract_emails_from_text,
    _fetch_page_text,
    _strip_html,
)
from src.integrations.google_search import BASE_URL, GoogleSearchClient


def test_extract_emails_from_text() -> None:
    assert _extract_emails_from_text("Contact: hello@uni.ac.kr") == ["hello@uni.ac.kr"]
    assert _extract_emails_from_text("No emails here") == []
    assert _extract_emails_from_text("a@example.com ignore") == []
    text = "info@lab.kr and admin@lab.kr and info@lab.kr"
    assert _extract_emails_from_text(text) == ["info@lab.kr", "admin@lab.kr"]


def test_extract_emails_filters_noise() -> None:
    text = "support@w3.org schema@schema.org real@university.ac.kr"
    result = _extract_emails_from_text(text)
    assert result == ["real@university.ac.kr"]


def test_strip_html() -> None:
    html = "<html><script>var x=1;</script><p>Hello world</p><style>.a{}</style></html>"
    text = _strip_html(html)
    assert "Hello world" in text
    assert "var x=1" not in text
    assert ".a{}" not in text


FAKE_CSE_RESPONSE = {
    "items": [
        {
            "title": "AI Research Lab - Seoul National University",
            "snippet": "Leading AI research in Korea...",
            "link": "https://ai.snu.ac.kr/contact",
        },
        {
            "title": "Korean AI Conference 2025",
            "snippet": "Annual conference on AI and ML...",
            "link": "https://kaic2025.org/about",
        },
        {
            "title": "Community Center",
            "snippet": "Local community services...",
            "link": "https://community.example.org/contact",
        },
    ]
}

FAKE_PAGE_SNU = """
<html><body>
<h1>AI Lab Contact</h1>
<p>Professor Kim: kim@ai.snu.ac.kr</p>
<footer>Office: admin@snu.ac.kr | Tel: 02-1234-5678</footer>
</body></html>
"""

FAKE_PAGE_CONF = """
<html><body>
<h1>KAIC 2025</h1>
<p>For inquiries: info@kaic2025.org</p>
</body></html>
"""

FAKE_PAGE_NO_EMAIL = """
<html><body>
<h1>Community Center</h1>
<p>Visit us at 123 Main Street.</p>
</body></html>
"""


def _mock_cse_api() -> None:
    respx.get(BASE_URL).mock(
        return_value=httpx.Response(200, json=FAKE_CSE_RESPONSE)
    )


def _mock_page_fetches() -> None:
    respx.get("https://ai.snu.ac.kr/contact").mock(
        return_value=httpx.Response(
            200, text=FAKE_PAGE_SNU, headers={"content-type": "text/html"}
        )
    )
    respx.get("https://kaic2025.org/about").mock(
        return_value=httpx.Response(
            200, text=FAKE_PAGE_CONF, headers={"content-type": "text/html"}
        )
    )
    respx.get("https://community.example.org/contact").mock(
        return_value=httpx.Response(
            200, text=FAKE_PAGE_NO_EMAIL, headers={"content-type": "text/html"}
        )
    )


@respx.mock
def test_google_search_source_discover() -> None:
    _mock_cse_api()
    _mock_page_fetches()

    client = GoogleSearchClient(api_key="test-key", cse_id="test-cx")
    source = GoogleSearchSource(client=client)
    results = source.discover({"query": "Korean university AI lab", "category": "university"})

    assert len(results) == 3
    assert results[0].name == "AI Research Lab - Seoul National University"
    assert results[0].email == "kim@ai.snu.ac.kr"
    assert results[0].source == "google_search"
    assert results[0].extra["category"] == "university"
    assert results[0].domain == "ai.snu.ac.kr"

    assert results[1].email == "info@kaic2025.org"
    assert results[1].extra["search_snippet"] == "Annual conference on AI and ML..."

    assert results[2].email is None


@respx.mock
def test_google_search_emails_with_category() -> None:
    _mock_cse_api()
    _mock_page_fetches()

    client = GoogleSearchClient(api_key="test-key", cse_id="test-cx")
    source = GoogleSearchSource(client=client)
    results = source.discover({"query": "test", "category": "religious"})

    for r in results:
        assert r.extra["category"] == "religious"
        assert r.extra["requires_review"] is True


@respx.mock
def test_google_search_page_emails_field() -> None:
    _mock_cse_api()
    _mock_page_fetches()

    client = GoogleSearchClient(api_key="test-key", cse_id="test-cx")
    source = GoogleSearchSource(client=client)
    results = source.discover({"query": "test"})

    snu = results[0]
    assert "kim@ai.snu.ac.kr" in snu.extra["page_emails"]
    assert "admin@snu.ac.kr" in snu.extra["page_emails"]


def test_google_search_source_no_config() -> None:
    source = GoogleSearchSource(client=None)
    source.client = None
    results = source.discover({"query": "test"})
    assert results == []


def test_google_search_source_requires_query() -> None:
    client = GoogleSearchClient(api_key="test-key", cse_id="test-cx")
    source = GoogleSearchSource(client=client)
    with pytest.raises(ValueError, match="query"):
        source.discover({})


@respx.mock
def test_google_search_domain_block_filter() -> None:
    _mock_cse_api()
    _mock_page_fetches()

    client = GoogleSearchClient(api_key="test-key", cse_id="test-cx")
    source = GoogleSearchSource(client=client)
    results = source.discover({
        "query": "test",
        "domains_block": ["ai.snu.ac.kr"],
    })

    domains = [r.domain for r in results]
    assert "ai.snu.ac.kr" not in domains


@respx.mock
def test_google_search_client_search() -> None:
    respx.get(BASE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"title": "Result 1", "snippet": "Snippet 1", "link": "https://a.com"},
                    {"title": "Result 2", "snippet": "Snippet 2", "link": "https://b.com"},
                ]
            },
        )
    )

    client = GoogleSearchClient(api_key="test-key", cse_id="test-cx")
    results = client.search("test query", num=5)

    assert len(results) == 2
    assert results[0]["title"] == "Result 1"
    assert results[1]["link"] == "https://b.com"


@respx.mock
def test_fetch_page_text_non_html() -> None:
    respx.get("https://example.com/file.pdf").mock(
        return_value=httpx.Response(
            200, content=b"PDF content", headers={"content-type": "application/pdf"}
        )
    )
    assert _fetch_page_text("https://example.com/file.pdf") == ""


@respx.mock
def test_fetch_page_text_timeout() -> None:
    respx.get("https://slow.example.com/").mock(side_effect=httpx.TimeoutException("timeout"))
    assert _fetch_page_text("https://slow.example.com/") == ""
