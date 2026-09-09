"""Integration tests: domain enrichment wired into InboundAgent."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents.inbound import (
    InboundAgent,
    ClassifyResult,
    DraftResult,
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
                # **회사를 비워 둡니다** — 허브스팟이 회사를 알면 이 분석은 아예 안 돕니다
                # (2026-09-09). 여기서 보려는 것은 「모를 때 분석이 초안까지 닿는가」입니다.
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


@patch("src.agents.domain_enrichment.fetch_homepage_meta")
def test_a_known_company_is_still_analyzed(mock_fetch, db_session):
    """**허브스팟에 회사가 적혀 있어도 홈페이지를 봅니다** (2026-09-09 실측으로 정함).

    한 번은 「회사를 알면 건너뛰자」로 바꿨다가 되돌렸습니다. 포털을 세어 보니 전제가
    틀렸습니다 (최근 연락처 100명):

        contact.company    18/100 (18%)
        contact.industry    0/100 (0%)     ← 「기업 종류」에 쓸 값이 저쪽에 없습니다
        회사 레코드 연결    37/100 · 그중 industry 1/36 · description 1/36

    허브스팟이 아는 것은 **이름뿐이고 그것도 82%가 비어 있습니다.** 건너뛰기로 아끼는
    것은 문의 다섯 건에 하나인데, 그 하나에서 잃는 것은 초안이 참고할 유일한 회사
    정보입니다. 그리고 비싸지도 않습니다 — 도메인당 90일 캐시라 새 회사에서만 돕니다.

    허브스팟이 아는 이름은 버리지 않고 `hint_company` 로 넘겨 모델이 홈페이지와
    대조하게 합니다.
    """
    from src.integrations.web_fetch import HomepageMeta

    mock_fetch.return_value = HomepageMeta(
        title="KnownCorp", description="Known stuff.", status="ok"
    )
    llm = _mock_llm()
    with patch("src.agents.inbound.SessionLocal", return_value=db_session),          patch("src.agents.domain_enrichment.SessionLocal", return_value=db_session):
        agent = InboundAgent(llm=llm, hubspot=None)
        result = agent.handle({
            "object_id": "hs-known-1",
            "occurred_at": "2026-09-09T10:00:00Z",
            "email": "buyer@knowncorp.com",
            "full_name": "Known Buyer",
            "company": "KnownCorp",          # ← 허브스팟이 이름은 압니다
            "last_message": "We want your product.",
        })

    assert result is not None
    mock_fetch.assert_called_once()
    analyze = next(c for c in llm.complete.call_args_list if "analyze_domain" in str(c[0][0]))
    assert analyze[0][1]["hint_company"] == "KnownCorp", "아는 이름은 힌트로 넘어가야 합니다"


@patch("src.agents.domain_enrichment.fetch_homepage_meta")
def test_the_web_search_only_runs_when_the_homepage_failed(mock_fetch, db_session):
    """**웹검색은 폴백입니다** — 홈페이지를 못 가져왔을 때만 (2026-09-09).

    주석은 처음부터 "fallback"이라고 적혀 있었는데 코드는 성공 여부와 무관하게 항상
    돌고 있었습니다. 이 호출은 **Pro 티어 웹검색**이라 인바운드 경로에서 가장 비싼
    한 번이고, 홈페이지가 잘 열린 건에서는 얹을 것이 거의 없습니다.
    """
    from src.agents.domain_enrichment import analyze_domain
    from src.integrations.web_fetch import HomepageMeta

    with patch("src.agents.domain_enrichment.SessionLocal", return_value=db_session):
        # ① 홈페이지가 열리면 검색은 안 합니다.
        mock_fetch.return_value = HomepageMeta(title="Fine", description="ok", status="ok")
        llm = _mock_llm()
        analyze_domain("goodsite.com", llm=llm)
        assert llm.search.call_count == 0

        # ② 막히면 검색이 대신합니다 — 그때는 도메인 말고 단서가 없습니다.
        mock_fetch.return_value = HomepageMeta(status="blocked")
        llm = _mock_llm()
        analyze_domain("blockedsite.com", llm=llm)
        assert llm.search.call_count == 1
