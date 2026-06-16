"""Domain enrichment — analyze a company from its email domain and cache results."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel

from ..common.config import settings
from ..common.domains import is_personal_domain
from ..db.models import DomainProfile
from ..db.session import SessionLocal
from ..integrations.web_fetch import HomepageMeta, fetch_homepage_meta
from ..llm.client import LLMClient

logger = logging.getLogger(__name__)

_MAX_AGE_DAYS = 90


def _clamp(value: str | None, limit: int) -> str | None:
    """Truncate a string to a column's max length (None stays None)."""
    if value is None:
        return None
    return value[:limit]


class DomainAnalysisResult(BaseModel):
    """LLM output schema for domain analysis."""

    company_name: str | None = None
    industry: str | None = None
    services: str | None = None
    target_market: str | None = None
    size_hint: str = "unknown"
    confidence: str = "low"
    notes: str | None = None


def analyze_domain(
    domain: str,
    *,
    llm: LLMClient | None = None,
    hint_company: str | None = None,
    force_refresh: bool = False,
) -> DomainProfile | None:
    """Analyze a domain and return a cached or freshly-created DomainProfile.

    Returns None for personal domains without doing any work.
    """
    domain = domain.lower().strip()
    if is_personal_domain(domain):
        logger.debug("Skipping personal domain: %s", domain)
        return None

    session = SessionLocal()
    try:
        if not force_refresh:
            existing = session.get(DomainProfile, domain)
            if existing is not None:
                # analyzed_at is stored naive-UTC (SQLite) — make it aware before
                # subtracting from an aware now() to avoid a TypeError.
                analyzed_at = existing.analyzed_at
                if analyzed_at.tzinfo is None:
                    analyzed_at = analyzed_at.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - analyzed_at).days
                if age_days <= _MAX_AGE_DAYS:
                    logger.info("Domain profile cache hit: %s", domain)
                    return existing
                logger.info("Domain profile stale (%d days): %s", age_days, domain)

        client = llm or LLMClient()
        meta = HomepageMeta(status="skipped")
        if settings.INBOUND_DOMAIN_HOMEPAGE_FETCH:
            try:
                meta = fetch_homepage_meta(domain)
            except Exception:
                logger.warning("Homepage fetch error for %s", domain, exc_info=True)
                meta = HomepageMeta(status="blocked")

        # Web-search grounding fallback — resolves well-known companies even when
        # their homepage is blocked/parked/timed out (the common failure we saw).
        search_findings = ""
        if settings.INBOUND_DOMAIN_SEARCH_GROUNDING:
            try:
                raw = client.search(
                    "inbound/search_company",
                    {"domain": domain, "hint_company": hint_company or ""},
                    tier="pro",
                )
                if isinstance(raw, str):
                    raw = raw.strip()
                    if raw and "no reliable information found" not in raw.lower():
                        search_findings = raw
            except Exception:
                logger.warning("Domain search grounding failed for %s", domain, exc_info=True)

        try:
            analysis = client.complete(
                "inbound/analyze_domain",
                {
                    "domain": domain,
                    "hint_company": hint_company or "",
                    "homepage_title": meta.title,
                    "homepage_description": meta.description or meta.og_description,
                    "homepage_keywords": meta.keywords,
                    "homepage_body": meta.body_text,
                    "fetch_status": meta.status,
                    "search_findings": search_findings or "(none)",
                },
                schema=DomainAnalysisResult,
            )
        except Exception:
            logger.warning("LLM domain analysis failed for %s", domain, exc_info=True)
            return None

        # Only force 'low' when we have NO signal at all — neither a successful
        # homepage fetch nor any search findings. With either, trust the model's
        # own confidence (the prompt forbids hallucinating beyond the evidence).
        if meta.status != "ok" and not search_findings:
            analysis.confidence = "low"

        provenance = []
        if meta.status == "ok":
            provenance.append("homepage")
        if search_findings:
            provenance.append("search")
        source = ("llm+" + "+".join(provenance)) if provenance else "llm_only"

        now = datetime.now(timezone.utc)
        profile = session.get(DomainProfile, domain)
        if profile is None:
            profile = DomainProfile(domain=domain, analyzed_at=now, updated_at=now)
            session.add(profile)
        else:
            profile.analyzed_at = now
            profile.updated_at = now

        # Clamp to the DomainProfile column limits. Grounded search output is
        # richer than the old homepage-only path and can overflow varchar(128)
        # (e.g. a long industry/target_market), so guard before persisting.
        profile.company_name = _clamp(analysis.company_name, 255)
        profile.industry = _clamp(analysis.industry, 128)
        profile.services = analysis.services  # Text column, unbounded
        profile.target_market = _clamp(analysis.target_market, 128)
        profile.size_hint = _clamp(analysis.size_hint, 64)
        profile.confidence = analysis.confidence
        profile.source = source
        profile.homepage_title = meta.title or None
        profile.homepage_description = (meta.description or meta.og_description) or None
        profile.homepage_fetch_status = meta.status
        profile.notes = analysis.notes

        session.commit()
        session.refresh(profile)
        logger.info(
            "Domain profile analyzed: %s industry=%s confidence=%s",
            domain,
            analysis.industry,
            analysis.confidence,
        )
        return profile
    finally:
        session.close()
