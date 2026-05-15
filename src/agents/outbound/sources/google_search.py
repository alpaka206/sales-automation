"""Google Custom Search source for outbound prospecting."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from ....integrations.email_discovery import (
    discover_emails_from_url,
    extract_emails_from_html,
)
from ....integrations.google_search import GoogleSearchClient, GoogleSearchNotConfigured
from .base import ProspectCandidate, SourceFilters, apply_common_filters

logger = logging.getLogger(__name__)


def _extract_emails_from_text(text: str) -> list[str]:
    """Extract emails from text via shared module."""
    return extract_emails_from_html(f"<p>{text}</p>")


def _fetch_page_text(url: str, timeout: int = 10) -> str:
    """Fetch a page and return stripped visible text (max 5000 chars)."""
    import httpx

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; SalesBot/1.0)"})
            r.raise_for_status()
            content_type = r.headers.get("content-type", "")
            if "text/html" not in content_type:
                return ""
            return _strip_html(r.text[:50000])
    except Exception:
        logger.debug("Failed to fetch %s", url)
        return ""


def _strip_html(html: str) -> str:
    """Remove tags, scripts, styles and return visible text."""
    import re as _re

    html = _re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=_re.DOTALL | _re.IGNORECASE)
    text = _re.sub(r"<[^>]+>", " ", html)
    text = _re.sub(r"\s+", " ", text)
    return text[:5000]


def _domain_from_url(url: str) -> str | None:
    """Extract domain from a URL."""
    try:
        parsed = urlparse(url)
        return parsed.hostname
    except Exception:
        return None


class GoogleSearchSource:
    """Discovers prospects via Google Custom Search, extracting contact emails from result pages."""

    name: str = "google_search"

    def __init__(self, client: GoogleSearchClient | None = None) -> None:
        try:
            self.client = client or GoogleSearchClient()
        except GoogleSearchNotConfigured:
            self.client = None

    def discover(self, filters: dict | None = None) -> list[ProspectCandidate]:
        """Search Google and extract prospects from result pages."""
        if not self.client:
            logger.warning("Google CSE not configured, skipping.")
            return []

        filters = filters or {}
        sf = SourceFilters(**{k: v for k, v in filters.items() if k in SourceFilters.model_fields})

        query = filters.get("query", "") or sf.extra.get("query", "")
        if not query:
            raise ValueError("Google search source requires 'query' filter.")

        category = filters.get("category", "other")
        max_results = min(filters.get("max_results", 10), 10)

        search_results = self.client.search(query, num=max_results)

        prospects: list[ProspectCandidate] = []
        for item in search_results:
            link = item.get("link", "")
            if not link:
                continue

            domain = _domain_from_url(link)
            page_text = _fetch_page_text(link)
            emails = _extract_emails_from_text(page_text)
            title = item.get("title", "")
            snippet = item.get("snippet", "")

            prospect = ProspectCandidate(
                name=title,
                email=emails[0] if emails else None,
                company=title,
                domain=domain,
                country=None,
                role=None,
                audience_size=None,
                source="google_search",
                source_ref=link,
                extra={
                    "category": category,
                    "search_snippet": snippet,
                    "page_emails": emails,
                    "requires_review": category == "religious",
                },
            )
            prospects.append(prospect)

        prospects = apply_common_filters(prospects, sf)
        logger.info(
            "GoogleSearch: found %d prospects for query '%s' (category=%s).",
            len(prospects),
            query,
            category,
        )
        return prospects
