"""Outbound prospect enrichment — fetches and summarizes company homepage via AI browser."""

from __future__ import annotations

import logging

from ...integrations.ai_browser import fetch_and_extract_sync
from ...llm.client import LLMClient
from .sources.base import ProspectCandidate

logger = logging.getLogger(__name__)


def enrich_prospect(candidate: ProspectCandidate, llm: LLMClient) -> dict:
    """Fetch company homepage and summarize it via AI browser. Returns empty dict on failure."""
    if not candidate.domain:
        return {}

    try:
        url = f"https://{candidate.domain}"
        result = fetch_and_extract_sync(
            url,
            extraction_prompt=(
                "이 회사 홈페이지를 분석해서 다음 정보를 추출해주세요:\n"
                "1. 회사가 뭘 하는지 2문장 이내 요약\n"
                "2. 페이지에서 발견되는 contact 이메일 목록\n\n"
                "Return JSON: {\"summary\": \"...\", \"contact_emails\": [\"...\"]}"
            ),
            max_html_chars=15000,
        )

        if result is None:
            return {}

        if isinstance(result, str):
            summary = result.strip()
            if len(summary) < 10:
                return {}
            return {"homepage_summary": summary, "enrichment_source": "ai_browser"}

        return {}

    except Exception:
        logger.debug("Enrichment failed for domain %s, continuing.", candidate.domain, exc_info=True)
        return {}
