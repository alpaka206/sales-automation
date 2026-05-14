"""YouTube Data API v3 client."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import httpx

from ..common.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://www.googleapis.com/youtube/v3"
QUOTA_FILE = os.path.join("data", "cache", "youtube", "usage.json")
DAILY_QUOTA_LIMIT = 10000


class YouTubeNotConfigured(RuntimeError):
    pass


class YouTubeQuotaExceeded(RuntimeError):
    pass


def _require_key() -> str:
    if not settings.YOUTUBE_API_KEY:
        raise YouTubeNotConfigured("YOUTUBE_API_KEY not set.")
    return settings.YOUTUBE_API_KEY


def _get_quota_usage() -> dict:
    if not os.path.exists(QUOTA_FILE):
        return {"date": "", "used": 0}
    with open(QUOTA_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_quota_usage(data: dict) -> None:
    os.makedirs(os.path.dirname(QUOTA_FILE), exist_ok=True)
    with open(QUOTA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _track_quota(cost: int) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage = _get_quota_usage()
    if usage.get("date") != today:
        usage = {"date": today, "used": 0}
    usage["used"] += cost
    _save_quota_usage(usage)
    if usage["used"] > DAILY_QUOTA_LIMIT * 0.9:
        logger.warning("YouTube quota at %d/%d for %s.", usage["used"], DAILY_QUOTA_LIMIT, today)


def _check_quota(cost: int) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage = _get_quota_usage()
    if usage.get("date") != today:
        return
    if usage["used"] + cost > DAILY_QUOTA_LIMIT:
        raise YouTubeQuotaExceeded(
            f"Would exceed daily quota ({usage['used']} + {cost} > {DAILY_QUOTA_LIMIT})."
        )


class YouTubeClient:
    """Thin wrapper around YouTube Data API v3."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or _require_key()

    def search_channels(
        self,
        query: str,
        region_code: str = "KR",
        max_results: int = 25,
    ) -> list[dict]:
        """Search for channels. Costs 100 quota units."""
        _check_quota(100)
        with httpx.Client(timeout=15) as client:
            r = client.get(
                f"{BASE_URL}/search",
                params={
                    "key": self.api_key,
                    "q": query,
                    "type": "channel",
                    "regionCode": region_code,
                    "maxResults": max_results,
                    "part": "snippet",
                },
            )
            r.raise_for_status()
        _track_quota(100)
        return r.json().get("items", [])

    def get_channel(self, channel_id: str) -> dict | None:
        """Get channel details. Costs 1 quota unit."""
        _check_quota(1)
        with httpx.Client(timeout=15) as client:
            r = client.get(
                f"{BASE_URL}/channels",
                params={
                    "key": self.api_key,
                    "id": channel_id,
                    "part": "snippet,statistics,brandingSettings",
                },
            )
            r.raise_for_status()
        _track_quota(1)
        items = r.json().get("items", [])
        return items[0] if items else None
