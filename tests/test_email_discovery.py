"""Tests for email discovery module."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from src.integrations.email_discovery import (
    _sort_by_domain,
    clear_cache,
    discover_emails_from_url,
    extract_emails_from_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_extract_plain_emails() -> None:
    html = _load_fixture("contact_plain.html")
    emails = extract_emails_from_html(html)
    assert "info@acmecorp.kr" in emails
    assert "sales@acmecorp.kr" in emails
    assert "support@acmecorp.kr" in emails
    assert len(emails) == 3


def test_extract_obfuscated_emails() -> None:
    html = _load_fixture("contact_obfuscated.html")
    emails = extract_emails_from_html(html)
    assert "info@techstartup.io" in emails
    assert "ceo@techstartup.io" in emails
    assert "hello@techstartup.io" in emails
    assert "support@techstartup.io" in emails


def test_extract_filters_noise() -> None:
    html = _load_fixture("contact_noisy.html")
    emails = extract_emails_from_html(html)
    assert "real@company.co.kr" in emails
    assert "hidden@company.co.kr" in emails
    noise = {"tracker@sentry.io", "schema@schema.org", "valid@example.com"}
    assert not noise.intersection(set(emails))


def test_extract_deduplicates() -> None:
    html = "<p>same@test.kr and same@test.kr and SAME@TEST.KR</p>"
    emails = extract_emails_from_html(html)
    assert emails == ["same@test.kr"]


def test_sort_by_domain() -> None:
    emails = ["random@other.com", "info@target.kr", "admin@target.kr", "x@third.io"]
    sorted_emails = _sort_by_domain(emails, "target.kr")
    assert sorted_emails[0] == "info@target.kr"
    assert sorted_emails[1] == "admin@target.kr"
    assert len(sorted_emails) == 4


@respx.mock
def test_discover_emails_from_url() -> None:
    clear_cache()
    html = _load_fixture("contact_plain.html")

    respx.get("https://acmecorp.kr/").mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )

    emails = discover_emails_from_url("https://acmecorp.kr/")
    assert "info@acmecorp.kr" in emails
    assert len(emails) >= 1


@respx.mock
def test_discover_follows_contact_link() -> None:
    clear_cache()
    main_html = _load_fixture("contact_with_links.html")
    contact_html = '<html><body><p>reach@followedlink.kr</p></body></html>'

    respx.get("https://company.kr/").mock(
        return_value=httpx.Response(200, text=main_html, headers={"content-type": "text/html"})
    )
    respx.get("https://company.kr/contact-us").mock(
        return_value=httpx.Response(200, text=contact_html, headers={"content-type": "text/html"})
    )

    emails = discover_emails_from_url("https://company.kr/")
    assert "reach@followedlink.kr" in emails


@respx.mock
def test_discover_domain_priority() -> None:
    clear_cache()
    html = '<html><body>random@other.com and info@target.kr</body></html>'

    respx.get("https://target.kr/").mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )

    emails = discover_emails_from_url(
        "https://target.kr/", prioritize_domain="target.kr"
    )
    assert emails[0] == "info@target.kr"


@respx.mock
def test_discover_caches_visited_urls() -> None:
    clear_cache()
    html = '<html><body>test@cached.kr</body></html>'

    respx.get("https://cached.kr/").mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )

    emails1 = discover_emails_from_url("https://cached.kr/")
    assert len(emails1) == 1

    emails2 = discover_emails_from_url("https://cached.kr/")
    assert emails2 == []


@respx.mock
def test_discover_handles_failure() -> None:
    clear_cache()
    respx.get("https://down.kr/").mock(
        return_value=httpx.Response(500, text="Error")
    )

    emails = discover_emails_from_url("https://down.kr/")
    assert emails == []


@respx.mock
def test_discover_skips_non_html() -> None:
    clear_cache()
    respx.get("https://files.kr/doc.pdf").mock(
        return_value=httpx.Response(
            200, content=b"PDF", headers={"content-type": "application/pdf"}
        )
    )

    emails = discover_emails_from_url("https://files.kr/doc.pdf")
    assert emails == []
