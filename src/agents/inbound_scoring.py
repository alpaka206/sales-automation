"""Pure helpers for inbound lead scoring, email normalization, and the
LLM enrichment-context block. Kept separate from the InboundAgent orchestration
so the scoring rules are easy to find, test, and tweak in isolation.
"""

from __future__ import annotations

import re

from ..common.domains import is_personal_domain

_PERSONAL_DOMAINS = {"gmail.com", "naver.com", "daum.net", "yahoo.com", "hotmail.com"}
_TARGET_COUNTRIES = {"kr", "korea", "jp", "japan", "sg", "th", "vn", "id", "ph", "my"}


def _normalize_email(email: str) -> str:
    local, _, domain = email.lower().partition("@")
    local = re.sub(r"\+.*$", "", local)
    return f"{local}@{domain}"


def _domain_from_email(email: str) -> str:
    return email.lower().split("@")[-1]


def _base_score(email: str | None, country: str | None, domain_profile: dict | None = None) -> int:
    score = 50
    if email:
        dom = _domain_from_email(email)
        if dom in _PERSONAL_DOMAINS or is_personal_domain(dom):
            score -= 10
        else:
            score += 15
    if country and country.lower() in _TARGET_COUNTRIES:
        score += 15
    if domain_profile:
        size = domain_profile.get("size_hint", "")
        if size in ("midmarket", "enterprise"):
            score += 5
    return max(0, min(100, score))


def _build_enrichment_context(contact_info: dict) -> str:
    """Build optional context block from HubSpot-enriched data."""
    parts: list[str] = []
    if contact_info.get("recent_emails"):
        parts.append(f"Recent email history with this contact:\n{contact_info['recent_emails']}")
    if contact_info.get("deal_summary"):
        parts.append(f"Associated deals:\n{contact_info['deal_summary']}")

    dp = contact_info.get("domain_profile")
    if dp:
        lines = [
            "Sender's domain profile (auto-analyzed):",
            f"- domain: {dp.get('domain', '')}",
            f"- inferred company: {dp.get('company_name', 'unknown')} (confidence: {dp.get('confidence', 'low')})",
            f"- industry: {dp.get('industry', 'unknown')}",
            f"- services: {dp.get('services', 'unknown')}",
            f"- target market: {dp.get('target_market', 'unknown')}",
            f"- size hint: {dp.get('size_hint', 'unknown')}",
        ]
        if dp.get("notes"):
            lines.append(f"- notes: {dp['notes']}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)
