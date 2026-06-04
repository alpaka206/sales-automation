"""LinkedIn comments source — extracts commenters from competitor post URLs."""

from __future__ import annotations

import logging
import re

from ....common.config import settings
from ....integrations.ai_browser import create_browser_context
from ....integrations.linkedin_profile import MAX_EMAIL_LOOKUPS_PER_RUN, fetch_profile_email
from .base import ProspectCandidate, parse_filters, apply_common_filters

logger = logging.getLogger(__name__)


class LinkedInCommentsSource:
    """Discovers prospects from commenters on LinkedIn posts."""

    name: str = "linkedin_comments"

    def discover(self, filters: dict | None = None) -> list[ProspectCandidate]:
        """Scrape commenters from the given post URLs."""
        if not settings.LINKEDIN_SCRAPING_ENABLED:
            raise NotImplementedError(
                "LinkedIn comment scraping is disabled. "
                "Set LINKEDIN_SCRAPING_ENABLED=true in .env to enable "
                "(operator responsibility for LinkedIn ToS compliance)."
            )

        filters = filters or {}
        post_urls = filters.get("post_urls", [])
        if not post_urls:
            raise ValueError("linkedin_comments source requires 'post_urls' filter.")

        max_per_post = filters.get("max_per_post", 50)
        _, sf = parse_filters(filters)

        if settings.LINKEDIN_API_TOKEN:
            prospects = self._discover_api(post_urls, max_per_post)
        else:
            prospects = self._discover_playwright(post_urls, max_per_post)

        if settings.LINKEDIN_SESSION_COOKIE:
            self._enrich_emails(prospects)

        prospects = apply_common_filters(prospects, sf)
        logger.info("LinkedInComments: found %d commenters from %d posts.", len(prospects), len(post_urls))
        return prospects

    def _enrich_emails(self, prospects: list[ProspectCandidate]) -> None:
        """Try to fetch emails from LinkedIn profiles for prospects that lack one."""
        candidates = [p for p in prospects if not p.email and p.extra.get("profile_url")]
        if not candidates:
            return

        lookups = min(len(candidates), MAX_EMAIL_LOOKUPS_PER_RUN)
        found = 0

        for prospect in candidates[:lookups]:
            profile_url = prospect.extra["profile_url"]
            try:
                email = fetch_profile_email(profile_url, settings.LINKEDIN_SESSION_COOKIE)
                if email:
                    prospect.email = email
                    found += 1
            except Exception:
                logger.debug("Email lookup failed for %s.", profile_url, exc_info=True)

        logger.info(
            "LinkedInComments: %d/%d email lookups returned email.", found, lookups
        )

    def _discover_api(self, post_urls: list[str], max_per_post: int) -> list[ProspectCandidate]:
        """Fetch commenters via official LinkedIn API (partner-tier)."""
        logger.info("Using LinkedIn API backend for comment scraping.")
        prospects: list[ProspectCandidate] = []

        for url in post_urls:
            post_id = _extract_post_id(url)
            if not post_id:
                logger.warning("Could not extract post ID from URL: %s", url)
                continue

            try:
                commenters = _fetch_commenters_api(post_id, max_per_post)
            except Exception:
                logger.warning("API fetch failed for post %s, skipping.", url, exc_info=True)
                continue

            for c in commenters:
                prospects.append(_to_candidate(c, url))

        return prospects

    def _discover_playwright(self, post_urls: list[str], max_per_post: int) -> list[ProspectCandidate]:
        """Scrape commenters via Playwright browser automation."""
        if not settings.LINKEDIN_SESSION_COOKIE:
            raise ValueError(
                "LINKEDIN_SESSION_COOKIE is required for Playwright scraping. "
                "Copy the li_at cookie from a logged-in LinkedIn browser session."
            )

        prospects: list[ProspectCandidate] = []

        with create_browser_context(cookies=[{
            "name": "li_at",
            "value": settings.LINKEDIN_SESSION_COOKIE,
            "domain": ".linkedin.com",
            "path": "/",
        }]) as context:
            for url in post_urls:
                try:
                    page_prospects = _scrape_post_comments(context, url, max_per_post)
                    prospects.extend(page_prospects)
                except Exception:
                    logger.warning("Playwright scrape failed for %s, skipping.", url, exc_info=True)

        return prospects


def _extract_post_id(url: str) -> str | None:
    """Extract the post/activity ID from a LinkedIn URL."""
    match = re.search(r"activity[:-](\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"/posts/[^/]+-(\d+)", url)
    if match:
        return match.group(1)
    return None


def _fetch_commenters_api(post_id: str, max_count: int) -> list[dict]:
    """Call LinkedIn API to get commenters. Requires partner-tier token."""
    import httpx

    resp = httpx.get(
        f"https://api.linkedin.com/v2/socialActions/urn:li:activity:{post_id}/comments",
        headers={"Authorization": f"Bearer {settings.LINKEDIN_API_TOKEN}"},
        params={"count": max_count},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    commenters: list[dict] = []
    for element in data.get("elements", []):
        actor = element.get("actor~", element.get("actor", {}))
        if isinstance(actor, str):
            continue
        commenters.append({
            "name": actor.get("localizedFirstName", "") + " " + actor.get("localizedLastName", ""),
            "profile_url": f"https://linkedin.com/in/{actor.get('vanityName', '')}",
            "headline": actor.get("localizedHeadline", ""),
            "comment_excerpt": (element.get("message", {}).get("text", ""))[:200],
        })

    return commenters


def _scrape_post_comments(context, url: str, max_count: int) -> list[ProspectCandidate]:
    """Scrape comments from a single LinkedIn post page."""
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    for _ in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)

    prospects: list[ProspectCandidate] = []
    comment_blocks = page.query_selector_all(".comments-comment-item, .comments-comment-entity")

    for block in comment_blocks[:max_count]:
        try:
            name_el = block.query_selector(
                ".comments-post-meta__name-text, "
                ".comments-comment-item__post-meta .hoverable-link-text"
            )
            name = name_el.inner_text().strip() if name_el else ""
            if not name:
                continue

            link_el = block.query_selector("a[href*='/in/']")
            profile_url = link_el.get_attribute("href") if link_el else ""

            headline_el = block.query_selector(
                ".comments-post-meta__headline, "
                ".comments-comment-item__post-meta .comments-post-meta__headline"
            )
            headline = headline_el.inner_text().strip() if headline_el else ""

            comment_el = block.query_selector(
                ".comments-comment-item__main-content, "
                ".comments-comment-texteditor .feed-shared-text"
            )
            comment_text = comment_el.inner_text().strip()[:200] if comment_el else ""

            company = _extract_company_from_headline(headline)

            prospects.append(
                ProspectCandidate(
                    name=name,
                    company=company,
                    role=headline or None,
                    source="linkedin_comments",
                    source_ref=url,
                    extra={
                        "profile_url": profile_url or "",
                        "headline": headline,
                        "comment_excerpt": comment_text,
                    },
                )
            )
        except Exception:
            logger.debug("Failed to parse a comment block, skipping.", exc_info=True)

    page.close()
    return prospects


def _extract_company_from_headline(headline: str) -> str | None:
    """Best-effort extraction of company from a LinkedIn headline like 'CTO at Acme Corp'."""
    for sep in [" at ", " @ ", " | "]:
        if sep in headline:
            return headline.split(sep, 1)[1].strip() or None
    return None


def _to_candidate(commenter: dict, post_url: str) -> ProspectCandidate:
    """Convert an API commenter dict to ProspectCandidate."""
    headline = commenter.get("headline", "")
    return ProspectCandidate(
        name=commenter.get("name", "").strip(),
        company=_extract_company_from_headline(headline),
        role=headline or None,
        source="linkedin_comments",
        source_ref=post_url,
        extra={
            "profile_url": commenter.get("profile_url", ""),
            "headline": headline,
            "comment_excerpt": commenter.get("comment_excerpt", ""),
        },
    )
