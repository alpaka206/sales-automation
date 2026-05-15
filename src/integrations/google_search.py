"""Google Custom Search API client."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import httpx

from ..common.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://www.googleapis.com/customsearch/v1"
QUOTA_FILE = os.path.join("data", "cache", "google_cse", "usage.json")
DAILY_QUOTA_LIMIT = 100


class GoogleSearchNotConfigured(RuntimeError):
    pass


class GoogleSearchQuotaExceeded(RuntimeError):
    pass


def _require_config() -> tuple[str, str]:
    """Return (api_key, cse_id) or raise."""
    if not settings.GOOGLE_CSE_API_KEY:
        raise GoogleSearchNotConfigured("GOOGLE_CSE_API_KEY not set.")
    if not settings.GOOGLE_CSE_ID:
        raise GoogleSearchNotConfigured("GOOGLE_CSE_ID not set.")
    return settings.GOOGLE_CSE_API_KEY, settings.GOOGLE_CSE_ID


def _get_quota_usage() -> dict:
    if not os.path.exists(QUOTA_FILE):
        return {"date": "", "used": 0}
    with open(QUOTA_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_quota_usage(data: dict) -> None:
    os.makedirs(os.path.dirname(QUOTA_FILE), exist_ok=True)
    with open(QUOTA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _check_quota(cost: int) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage = _get_quota_usage()
    if usage.get("date") != today:
        return
    if usage["used"] + cost > DAILY_QUOTA_LIMIT:
        raise GoogleSearchQuotaExceeded(
            f"Would exceed daily quota ({usage['used']} + {cost} > {DAILY_QUOTA_LIMIT})."
        )


def _track_quota(cost: int) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage = _get_quota_usage()
    if usage.get("date") != today:
        usage = {"date": today, "used": 0}
    usage["used"] += cost
    _save_quota_usage(usage)
    if usage["used"] > DAILY_QUOTA_LIMIT * 0.9:
        logger.warning(
            "Google CSE quota at %d/%d for %s.", usage["used"], DAILY_QUOTA_LIMIT, today
        )


class GoogleSearchClient:
    """Thin wrapper around Google Custom Search JSON API."""

    def __init__(
        self, api_key: str | None = None, cse_id: str | None = None
    ) -> None:
        if api_key and cse_id:
            self.api_key = api_key
            self.cse_id = cse_id
        else:
            self.api_key, self.cse_id = _require_config()

    def search(self, query: str, num: int = 10) -> list[dict]:
        """Run a search query. Each call costs 1 quota unit. Returns list of {title, snippet, link}."""
        num = min(num, 10)
        _check_quota(1)
        with httpx.Client(timeout=15) as client:
            r = client.get(
                BASE_URL,
                params={
                    "key": self.api_key,
                    "cx": self.cse_id,
                    "q": query,
                    "num": num,
                },
            )
            r.raise_for_status()
        _track_quota(1)
        items = r.json().get("items", [])
        return [
            {
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "link": item.get("link", ""),
            }
            for item in items
        ]
