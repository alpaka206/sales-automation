"""Outbound prospect enrichment — fetches and summarizes company homepage."""

from __future__ import annotations

import logging
import re

import httpx

from ...llm.client import LLMClient
from .sources.base import ProspectCandidate

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 3000
FETCH_TIMEOUT = 5.0


def _strip_html(html: str) -> str:
    """Extract visible text from HTML."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_TEXT_CHARS]


def enrich_prospect(candidate: ProspectCandidate, llm: LLMClient) -> dict:
    """Fetch company homepage and summarize it. Returns empty dict on failure."""
    if not candidate.domain:
        return {}

    try:
        url = f"https://{candidate.domain}"
        with httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=True) as cx:
            resp = cx.get(url, headers={"Accept": "text/html"})
            resp.raise_for_status()

        visible_text = _strip_html(resp.text)
        if len(visible_text) < 50:
            return {}

        summary = llm.complete(
            "outbound/enrich_homepage",
            {"domain": candidate.domain, "homepage_text": visible_text},
        )
        return {"homepage_summary": summary, "enrichment_source": "homepage"}

    except Exception:
        logger.debug("Enrichment failed for domain %s, continuing.", candidate.domain, exc_info=True)
        return {}
