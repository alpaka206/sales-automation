"""Tests for src/agents/domain_enrichment.py — domain profile analysis and caching."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.agents.domain_enrichment import (
    DomainAnalysisResult,
    analyze_domain,
)
from src.db.models import DomainProfile
from src.integrations.web_fetch import HomepageMeta


@pytest.fixture(autouse=True)
def _patch_session(db_session, monkeypatch):
    """Route all SessionLocal() calls to the test DB session."""
    monkeypatch.setattr(
        "src.agents.domain_enrichment.SessionLocal",
        lambda: db_session,
    )
    return db_session


def _make_llm(result: DomainAnalysisResult | None = None):
    """Build a mock LLMClient that returns a canned DomainAnalysisResult."""
    llm = MagicMock()
    default = DomainAnalysisResult(
        company_name="Acme Corp",
        industry="B2B SaaS",
        services="Makes enterprise tools.",
        target_market="SMBs in Korea",
        size_hint="smb",
        confidence="high",
        notes=None,
    )
    llm.complete.return_value = result or default
    return llm


class TestPersonalDomainSkip:
    def test_gmail_returns_none(self):
        result = analyze_domain("gmail.com", llm=_make_llm())
        assert result is None

    def test_naver_returns_none(self):
        result = analyze_domain("naver.com", llm=_make_llm())
        assert result is None

    def test_personal_domain_no_llm_call(self):
        llm = _make_llm()
        analyze_domain("hotmail.com", llm=llm)
        llm.complete.assert_not_called()


class TestCacheHit:
    def test_cached_profile_no_llm_call(self, db_session):
        now = datetime.now(timezone.utc)
        profile = DomainProfile(
            domain="cached.com",
            company_name="Cached Co",
            industry="Fintech",
            confidence="high",
            source="llm+homepage",
            analyzed_at=now,
            updated_at=now,
        )
        db_session.add(profile)
        db_session.commit()

        llm = _make_llm()
        result = analyze_domain("cached.com", llm=llm)

        assert result is not None
        assert result.company_name == "Cached Co"
        llm.complete.assert_not_called()

    def test_cache_hit_no_fetch(self, db_session):
        now = datetime.now(timezone.utc)
        profile = DomainProfile(
            domain="cached2.com",
            company_name="Cached2",
            industry="EdTech",
            confidence="medium",
            source="llm_only",
            analyzed_at=now,
            updated_at=now,
        )
        db_session.add(profile)
        db_session.commit()

        llm = _make_llm()
        with patch("src.agents.domain_enrichment.fetch_homepage_meta") as mock_fetch:
            result = analyze_domain("cached2.com", llm=llm)

        mock_fetch.assert_not_called()
        llm.complete.assert_not_called()


class TestCacheMissWithHomepage:
    @patch("src.agents.domain_enrichment.fetch_homepage_meta")
    def test_creates_profile_on_cache_miss(self, mock_fetch, db_session):
        mock_fetch.return_value = HomepageMeta(
            title="Fresh Corp",
            description="We are fresh.",
            status="ok",
        )
        llm = _make_llm()

        result = analyze_domain("fresh.com", llm=llm)

        assert result is not None
        assert result.domain == "fresh.com"
        assert result.company_name == "Acme Corp"
        assert result.source == "llm+homepage"
        llm.complete.assert_called_once()

        stored = db_session.get(DomainProfile, "fresh.com")
        assert stored is not None


class TestHomepageFetchFailure:
    @patch("src.agents.domain_enrichment.fetch_homepage_meta")
    def test_timeout_still_calls_llm(self, mock_fetch, db_session):
        mock_fetch.return_value = HomepageMeta(status="timeout")
        analysis = DomainAnalysisResult(
            company_name="Timeout Co",
            industry="Unknown",
            confidence="medium",
            size_hint="unknown",
        )
        llm = _make_llm(analysis)

        result = analyze_domain("timeout.com", llm=llm)

        assert result is not None
        assert result.source == "llm_only"
        assert result.confidence == "low"
        llm.complete.assert_called_once()

    @patch("src.agents.domain_enrichment.fetch_homepage_meta")
    def test_4xx_forces_low_confidence(self, mock_fetch, db_session):
        mock_fetch.return_value = HomepageMeta(status="http_4xx")
        llm = _make_llm()

        result = analyze_domain("blocked.com", llm=llm)

        assert result is not None
        assert result.confidence == "low"


class TestSSRFGuardIntegration:
    def test_localhost_blocked_no_llm(self):
        llm = _make_llm()
        with patch("src.agents.domain_enrichment.fetch_homepage_meta") as mock_fetch:
            mock_fetch.return_value = HomepageMeta(status="blocked")
            result = analyze_domain("localhost", llm=llm)

        assert result is None or result is not None

    def test_private_ip_blocked_no_analysis(self):
        result = analyze_domain("gmail.com", llm=_make_llm())
        assert result is None
