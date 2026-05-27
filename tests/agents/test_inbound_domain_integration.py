"""Integration tests: domain enrichment wired into InboundAgent."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents.inbound import (
    InboundAgent,
    ClassifyResult,
    DraftResult,
    ScoreAdjustResult,
    _base_score,
    _build_enrichment_context,
    _processed,
)
from src.db.models import DomainProfile, Message
from src.llm import knowledge


@pytest.fixture(autouse=True)
def _clear_dedup():
    _processed.clear()
    yield
    _processed.clear()


@pytest.fixture(autouse=True)
def _isolated_knowledge_db(db_session, monkeypatch):
    factory = lambda: db_session  # noqa: E731
    monkeypatch.setattr(knowledge, "SessionLocal", factory)
    knowledge.reset_cache()
    yield db_session
    knowledge.reset_cache()


def _mock_llm():
    llm = MagicMock()

    def side_effect(prompt_name, variables=None, schema=None, **kw):
        if "analyze_domain" in prompt_name:
            from src.agents.domain_enrichment import DomainAnalysisResult

            return DomainAnalysisResult(
                company_name="TestCorp",
                industry="B2B SaaS",
                services="Testing tools.",
                target_market="Developers",
                size_hint="midmarket",
                confidence="high",
                notes=None,
            )
        if "classify" in prompt_name:
            return ClassifyResult(category="purchase_inquiry", reasoning="Wants to buy")
        if "score_adjust" in prompt_name:
            return ScoreAdjustResult(adjustment=5, reasoning="Good fit")
        if "draft_reply" in prompt_name:
            return DraftResult(
                subject="Re: Inquiry",
                body="Thank you.",
                language="ko",
            )
        return "ok"

    llm.complete = MagicMock(side_effect=side_effect)
    return llm


class TestEnrichmentContextIncluded:
    def test_domain_profile_in_enrichment_context(self):
        contact_info = {
            "recent_emails": "",
            "deal_summary": "",
            "domain_profile": {
                "domain": "acme.com",
                "company_name": "Acme Corp",
                "industry": "B2B SaaS",
                "services": "Enterprise tools.",
                "target_market": "SMBs",
                "size_hint": "enterprise",
                "confidence": "high",
                "notes": "Well-known player.",
            },
        }
        ctx = _build_enrichment_context(contact_info)
        assert "Sender's domain profile" in ctx
        assert "acme.com" in ctx
        assert "Acme Corp" in ctx
        assert "B2B SaaS" in ctx
        assert "enterprise" in ctx

    def test_no_domain_profile_no_block(self):
        contact_info = {
            "recent_emails": "",
            "deal_summary": "",
            "domain_profile": None,
        }
        ctx = _build_enrichment_context(contact_info)
        assert "domain profile" not in ctx.lower()


class TestBaseScoreWithDomainProfile:
    def test_midmarket_adds_5(self):
        score = _base_score("ceo@company.com", "korea", {"size_hint": "midmarket"})
        assert score == 85  # 50 + 15 (enterprise domain) + 15 (country) + 5 (midmarket)

    def test_enterprise_adds_5(self):
        score = _base_score("ceo@company.com", "us", {"size_hint": "enterprise"})
        assert score == 70  # 50 + 15 (enterprise domain) + 5 (enterprise)

    def test_startup_no_bonus(self):
        score = _base_score("ceo@company.com", "us", {"size_hint": "startup"})
        assert score == 65  # 50 + 15 (enterprise domain)

    def test_none_profile_no_bonus(self):
        score = _base_score("ceo@company.com", "us", None)
        assert score == 65  # 50 + 15 (enterprise domain)


class TestInboundWithDomainEnrichment:
    @patch("src.agents.domain_enrichment.fetch_homepage_meta")
    def test_enrichment_appears_in_draft_prompt(self, mock_fetch, db_session):
        from src.integrations.web_fetch import HomepageMeta

        mock_fetch.return_value = HomepageMeta(
            title="TestCorp", description="Test stuff.", status="ok"
        )

        llm = _mock_llm()
        with patch("src.agents.inbound.SessionLocal", return_value=db_session), \
             patch("src.agents.domain_enrichment.SessionLocal", return_value=db_session):
            agent = InboundAgent(llm=llm, hubspot=None)
            result = agent.handle({
                "object_id": "hs-enrich-1",
                "occurred_at": "2026-05-27T10:00:00Z",
                "email": "buyer@testcorp.com",
                "full_name": "Test Buyer",
                "company": "TestCorp",
                "country": "korea",
                "last_message": "We want your product.",
            })

        assert result is not None
        assert result["category"] == "purchase_inquiry"

        draft_call = next(
            c for c in llm.complete.call_args_list if "draft_reply" in str(c[0][0])
        )
        enrichment = draft_call[0][1]["enrichment_context"]
        assert "domain profile" in enrichment.lower()
        assert "testcorp.com" in enrichment.lower()

    @patch("src.agents.domain_enrichment.fetch_homepage_meta")
    def test_enrichment_failure_does_not_break_inbound(self, mock_fetch, db_session):
        mock_fetch.side_effect = Exception("network error")

        llm = _mock_llm()
        with patch("src.agents.inbound.SessionLocal", return_value=db_session), \
             patch("src.agents.domain_enrichment.SessionLocal", return_value=db_session):
            agent = InboundAgent(llm=llm, hubspot=None)
            result = agent.handle({
                "object_id": "hs-fail-1",
                "occurred_at": "2026-05-27T11:00:00Z",
                "email": "buyer@failcorp.com",
                "full_name": "Fail Buyer",
                "last_message": "Hello!",
            })

        assert result is not None
        messages = db_session.query(Message).filter_by(status="pending_approval").all()
        assert len(messages) >= 1

    def test_personal_domain_no_enrichment(self, db_session):
        llm = _mock_llm()
        with patch("src.agents.inbound.SessionLocal", return_value=db_session):
            agent = InboundAgent(llm=llm, hubspot=None)
            result = agent.handle({
                "object_id": "hs-personal-1",
                "occurred_at": "2026-05-27T12:00:00Z",
                "email": "user@gmail.com",
                "full_name": "Personal User",
                "last_message": "Question about pricing.",
            })

        assert result is not None
        analyze_calls = [
            c for c in llm.complete.call_args_list if "analyze_domain" in str(c[0][0])
        ]
        assert len(analyze_calls) == 0

        stored = db_session.query(DomainProfile).count()
        assert stored == 0
