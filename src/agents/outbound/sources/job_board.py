"""Job board source for outbound prospecting (사람인·잡코리아 via Google CSE)."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from ....integrations.email_discovery import extract_emails_from_html
from ....integrations.google_search import GoogleSearchClient, GoogleSearchNotConfigured
from .base import ProspectCandidate, SourceFilters, apply_common_filters
from .google_search import _fetch_page_text

logger = logging.getLogger(__name__)

_DEFAULT_SITES = "saramin.co.kr,jobkorea.co.kr"

_PREFIX_RE = re.compile(r"^(?:\(주\)|㈜)\s*", re.UNICODE)
_SEPARATOR_RE = re.compile(r"^(.+?)\s*[-|·]\s", re.UNICODE)
_RECRUIT_RE = re.compile(r"^(.+?)\s+채용", re.UNICODE)


def _extract_company_from_title(title: str) -> str | None:
    """Try to extract company name from a job posting title."""
    stripped = _PREFIX_RE.sub("", title).strip()
    if not stripped:
        return None

    candidates: list[str] = []

    m = _SEPARATOR_RE.search(stripped)
    if m and len(m.group(1).strip()) >= 2:
        candidates.append(m.group(1).strip())

    m = _RECRUIT_RE.search(stripped)
    if m and len(m.group(1).strip()) >= 2:
        candidates.append(m.group(1).strip())

    if _PREFIX_RE.match(title):
        first_token = stripped.split()[0]
        if len(first_token) >= 2:
            candidates.append(first_token)

    if candidates:
        return min(candidates, key=len)

    return None


def _extract_domain_from_text(text: str) -> str | None:
    """Try to extract a company homepage domain from page text."""
    url_re = re.compile(r"https?://(www\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", re.IGNORECASE)
    skip_domains = {
        "saramin.co.kr", "jobkorea.co.kr", "wanted.co.kr",
        "google.com", "facebook.com", "instagram.com",
        "twitter.com", "youtube.com", "naver.com",
        "kakao.com", "linkedin.com", "x.com",
    }
    for m in url_re.finditer(text):
        domain = m.group(2).lower()
        if not any(domain.endswith(s) for s in skip_domains):
            return domain
    return None


class JobBoardSource:
    """Discovers prospects from Korean job boards via Google Custom Search."""

    name: str = "job_board"

    def __init__(self, client: GoogleSearchClient | None = None) -> None:
        try:
            self.client = client or GoogleSearchClient()
        except GoogleSearchNotConfigured:
            self.client = None

    def discover(self, filters: dict | None = None) -> list[ProspectCandidate]:
        """Search job boards and extract company prospects."""
        if not self.client:
            logger.warning("Google CSE not configured, skipping job board source.")
            return []

        filters = filters or {}
        sf = SourceFilters(**{k: v for k, v in filters.items() if k in SourceFilters.model_fields})

        keyword = filters.get("keyword", "") or filters.get("query", "")
        if not keyword:
            raise ValueError("Job board source requires 'keyword' filter.")

        sites_str = filters.get("sites", _DEFAULT_SITES)
        sites = [s.strip() for s in sites_str.split(",") if s.strip()]
        max_results = min(filters.get("max_results", 10), 10)

        prospects: list[ProspectCandidate] = []
        seen_links: set[str] = set()

        for site in sites:
            query = f'site:{site} "{keyword}"'
            try:
                search_results = self.client.search(query, num=max_results)
            except Exception:
                logger.warning("CSE search failed for site %s, skipping.", site)
                continue

            for item in search_results:
                link = item.get("link", "")
                if not link or link in seen_links:
                    continue
                seen_links.add(link)

                title = item.get("title", "")
                snippet = item.get("snippet", "")
                company = _extract_company_from_title(title)

                page_text = _fetch_page_text(link)
                emails = extract_emails_from_html(page_text) if page_text else []
                company_domain = _extract_domain_from_text(page_text) if page_text else None

                source_site = urlparse(link).hostname or site

                prospects.append(
                    ProspectCandidate(
                        name=company or title,
                        email=emails[0] if emails else None,
                        company=company or title,
                        domain=company_domain,
                        country="KR",
                        role=keyword,
                        audience_size=None,
                        source="job_board",
                        source_ref=link,
                        extra={
                            "job_title": title,
                            "job_snippet": snippet,
                            "job_site": source_site,
                            "page_emails": emails,
                        },
                    )
                )

        prospects = apply_common_filters(prospects, sf)
        logger.info(
            "JobBoard: found %d prospects for keyword '%s'.",
            len(prospects),
            keyword,
        )
        return prospects
