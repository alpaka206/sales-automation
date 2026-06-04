"""YouTube channel source for outbound prospecting."""

from __future__ import annotations

import logging
import re

from ....integrations.youtube import YouTubeClient, YouTubeNotConfigured
from .base import ProspectCandidate, parse_filters, apply_common_filters

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _extract_email(text: str) -> str | None:
    match = _EMAIL_RE.search(text)
    return match.group(0).lower() if match else None


class YouTubeSource:
    """Discovers prospects from YouTube channel search."""

    name: str = "youtube"

    def __init__(self, client: YouTubeClient | None = None) -> None:
        try:
            self.client = client or YouTubeClient()
        except YouTubeNotConfigured:
            self.client = None

    def discover(self, filters: dict | None = None) -> list[ProspectCandidate]:
        """Search YouTube channels and extract prospect data."""
        if not self.client:
            logger.warning("YouTube API key not configured, skipping.")
            return []

        filters = filters or {}
        _, sf = parse_filters(filters)

        query = filters.get("query", "") or sf.extra.get("query", "")
        if not query:
            raise ValueError("YouTube source requires 'query' filter.")

        region = filters.get("region_code", "KR")
        max_results = filters.get("max_results", 25)
        min_subs = sf.min_audience or filters.get("min_subscribers", 0)

        search_results = self.client.search_channels(query, region, max_results)

        prospects: list[ProspectCandidate] = []
        for item in search_results:
            channel_id = item.get("id", {}).get("channelId", "")
            if not channel_id:
                continue

            detail = self.client.get_channel(channel_id)
            if not detail:
                continue

            stats = detail.get("statistics", {})
            sub_count = int(stats.get("subscriberCount", "0"))
            if sub_count < min_subs:
                continue

            snippet = detail.get("snippet", {})
            description = snippet.get("description", "")
            email = _extract_email(description)
            title = snippet.get("title", "")
            country = snippet.get("country", "")

            prospects.append(
                ProspectCandidate(
                    name=title,
                    email=email,
                    company=title,
                    domain=None,
                    country=country,
                    role="youtube_channel",
                    audience_size=sub_count,
                    source="youtube",
                    source_ref=channel_id,
                    extra={
                        "subscribers": sub_count,
                        "description_snippet": description[:200],
                    },
                )
            )

        prospects = apply_common_filters(prospects, sf)
        logger.info("YouTube: found %d prospects for query '%s'.", len(prospects), query)
        return prospects
