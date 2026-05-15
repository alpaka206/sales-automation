"""Reusable email extraction from web pages with obfuscation handling."""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

_OBFUSCATION_PATTERNS = [
    re.compile(
        r"([\w.+-]+)\s*[\[\(]\s*at\s*[\]\)]\s*([\w-]+)\s*[\[\(]\s*dot\s*[\]\)]\s*([\w.-]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"([\w.+-]+)\s+at\s+([\w-]+)\s+dot\s+([\w.-]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r'mailto:\s*([\w.+-]+@[\w-]+\.[\w.-]+)',
        re.IGNORECASE,
    ),
]

_NOISE_DOMAINS = frozenset({
    "example.com", "example.org", "example.net",
    "sentry.io", "w3.org", "schema.org",
    "googleapis.com", "google.com", "gstatic.com",
    "wixpress.com", "squarespace.com",
})

_CONTACT_LINK_RE = re.compile(
    r'href=["\']([^"\']*(?:contact|about|team|문의|연락)[^"\']*)["\']',
    re.IGNORECASE,
)

_visited_cache: set[str] = set()


def extract_emails_from_html(html: str) -> list[str]:
    """Extract emails from HTML, handling obfuscation patterns."""
    text = _strip_html(html)
    found: list[str] = []
    seen: set[str] = set()

    for pattern in _OBFUSCATION_PATTERNS:
        for m in pattern.finditer(text):
            if pattern.groups == 3:
                email = f"{m.group(1)}@{m.group(2)}.{m.group(3)}".lower()
            else:
                email = m.group(1).lower()
            if _is_valid_email(email) and email not in seen:
                seen.add(email)
                found.append(email)

    for m in _EMAIL_RE.finditer(text):
        email = m.group(0).lower()
        if _is_valid_email(email) and email not in seen:
            seen.add(email)
            found.append(email)

    for m in _EMAIL_RE.finditer(html):
        email = m.group(0).lower()
        if _is_valid_email(email) and email not in seen:
            seen.add(email)
            found.append(email)

    return found


def discover_emails_from_url(
    url: str,
    timeout: int = 5,
    prioritize_domain: str | None = None,
) -> list[str]:
    """Fetch a URL and extract emails, following one level of contact/about links."""
    if url in _visited_cache:
        return []

    _visited_cache.add(url)
    emails: list[str] = []
    seen: set[str] = set()

    html = _fetch_html(url, timeout)
    if not html:
        return []

    page_emails = extract_emails_from_html(html)
    for e in page_emails:
        if e not in seen:
            seen.add(e)
            emails.append(e)

    contact_links = _CONTACT_LINK_RE.findall(html)
    for href in contact_links[:3]:
        full_url = urljoin(url, href)
        if full_url in _visited_cache:
            continue
        _visited_cache.add(full_url)

        sub_html = _fetch_html(full_url, timeout)
        if sub_html:
            for e in extract_emails_from_html(sub_html):
                if e not in seen:
                    seen.add(e)
                    emails.append(e)

    if prioritize_domain:
        emails = _sort_by_domain(emails, prioritize_domain)

    return emails


def clear_cache() -> None:
    """Clear the visited URL cache."""
    _visited_cache.clear()


def _is_valid_email(email: str) -> bool:
    """Check if email is not noise."""
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1]
    return domain not in _NOISE_DOMAINS


def _sort_by_domain(emails: list[str], domain: str) -> list[str]:
    """Sort emails so those matching the given domain come first."""
    domain = domain.lower()
    matching = [e for e in emails if e.rsplit("@", 1)[1] == domain]
    others = [e for e in emails if e.rsplit("@", 1)[1] != domain]
    return matching + others


def _fetch_html(url: str, timeout: int) -> str:
    """Fetch a page and return raw HTML."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible; SalesBot/1.0)"}
            )
            r.raise_for_status()
            if "text/html" not in r.headers.get("content-type", ""):
                return ""
            return r.text[:100000]
    except Exception:
        logger.debug("Failed to fetch %s", url)
        return ""


def _strip_html(html: str) -> str:
    """Remove tags, scripts, styles and return visible text."""
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text[:10000]
