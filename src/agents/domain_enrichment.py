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
                age_days = (datetime.now(timezone.utc) - existing.analyzed_at).days
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

        try:
            analysis = client.complete(
                "inbound/analyze_domain",
                {
                    "domain": domain,
                    "hint_company": hint_company or "",
                    "homepage_title": meta.title,
                    "homepage_description": meta.description or meta.og_description,
                    "homepage_keywords": meta.keywords,
                    "fetch_status": meta.status,
                },
                schema=DomainAnalysisResult,
            )
        except Exception:
            logger.warning("LLM domain analysis failed for %s", domain, exc_info=True)
            return None

        if meta.status != "ok":
            analysis.confidence = "low"

        source = "llm+homepage" if meta.status == "ok" else "llm_only"

        now = datetime.now(timezone.utc)
        profile = session.get(DomainProfile, domain)
        if profile is None:
            profile = DomainProfile(domain=domain, analyzed_at=now, updated_at=now)
            session.add(profile)
        else:
            profile.analyzed_at = now
            profile.updated_at = now

        profile.company_name = analysis.company_name
        profile.industry = analysis.industry
        profile.services = analysis.services
        profile.target_market = analysis.target_market
        profile.size_hint = analysis.size_hint
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
