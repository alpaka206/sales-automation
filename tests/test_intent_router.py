"""Tests for the natural language → outbound source intent router."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents.outbound.dispatcher import (
    IntentRouterResult,
    dispatch_natural_query,
    route_intent,
)
from src.db.models import OutboundIntent


# ---------------------------------------------------------------------------
# Unit: IntentRouterResult validation
# ---------------------------------------------------------------------------

class TestIntentRouterResult:
    def test_youtube_example(self):
        r = IntentRouterResult(
            source="youtube",
            filters={"query": "의료기기", "min_subscribers": 100000},
            confidence=0.92,
            rationale="유튜브 구독자 기반 검색이 명확하게 요청됨",
        )
        assert r.source == "youtube"
        assert r.filters["min_subscribers"] == 100000
        assert r.requires_user_input == []

    def test_job_board_example(self):
        r = IntentRouterResult(
            source="job_board",
            filters={"keyword": "성형외과 마케팅"},
            confidence=0.88,
            rationale="채용 공고 키워드 검색에 적합",
        )
        assert r.source == "job_board"
        assert r.confidence == 0.88

    def test_requires_user_input(self):
        r = IntentRouterResult(
            source="linkedin_comments",
            filters={},
            confidence=0.75,
            rationale="포스트 URL 필요",
            requires_user_input=["post_urls: LinkedIn 포스트 URL을 제공해주세요"],
        )
        assert len(r.requires_user_input) == 1

    def test_invalid_source_rejected(self):
        with pytest.raises(Exception):
            IntentRouterResult(
                source="unknown_source",
                filters={},
                confidence=0.5,
                rationale="bad",
            )


# ---------------------------------------------------------------------------
# Unit: route_intent calls LLM correctly
# ---------------------------------------------------------------------------

class TestRouteIntent:
    def test_calls_llm_with_correct_prompt(self, mock_llm):
        expected = IntentRouterResult(
            source="youtube",
            filters={"query": "의료기기"},
            confidence=0.9,
            rationale="ok",
        )
        mock_llm.complete.return_value = expected

        result = route_intent(mock_llm, "의료기기 유튜브 채널")

        mock_llm.complete.assert_called_once_with(
            "outbound/intent_router",
            {"user_query": "의료기기 유튜브 채널"},
            schema=IntentRouterResult,
        )
        assert result.source == "youtube"


# ---------------------------------------------------------------------------
# Integration: dispatch_natural_query
# ---------------------------------------------------------------------------

class TestDispatchNaturalQuery:
    @patch("src.agents.outbound.dispatcher.SessionLocal")
    def test_low_confidence_rejected(self, mock_session_cls, mock_llm):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        mock_llm.complete.return_value = IntentRouterResult(
            source="google_search",
            filters={},
            confidence=0.15,
            rationale="의도가 너무 모호함",
        )

        result = dispatch_natural_query(mock_llm, "사람 찾아줘")

        assert result["status"] == "rejected"
        assert result["confidence"] == 0.15
        mock_session.add.assert_called_once()
        saved = mock_session.add.call_args[0][0]
        assert isinstance(saved, OutboundIntent)
        assert saved.status == "failed"

    @patch("src.agents.outbound.dispatcher.SessionLocal")
    def test_pending_user_input(self, mock_session_cls, mock_llm):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        mock_llm.complete.return_value = IntentRouterResult(
            source="linkedin_comments",
            filters={},
            confidence=0.75,
            rationale="URL 필요",
            requires_user_input=["post_urls: LinkedIn 포스트 URL을 제공해주세요"],
        )

        result = dispatch_natural_query(mock_llm, "LinkedIn 포스트 댓글 수집")

        assert result["status"] == "pending_user_input"
        assert "post_urls" in result["requires_user_input"][0]
        assert result["routed_source"] == "linkedin_comments"

    @patch("src.agents.outbound.dispatcher.SessionLocal")
    @patch("src.agents.outbound.agent.OutboundAgent.run")
    def test_dispatched_successfully(self, mock_run, mock_session_cls, mock_llm):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_run.return_value = {"total": 5, "skipped_dup": 1, "skipped_lowscore": 0, "drafted": 4}

        mock_llm.complete.return_value = IntentRouterResult(
            source="youtube",
            filters={"query": "의료기기", "min_subscribers": 100000},
            confidence=0.92,
            rationale="유튜브 검색 의도 명확",
        )

        result = dispatch_natural_query(mock_llm, "구독자 10만+ 의료기기 유튜브 채널")

        assert result["status"] == "dispatched"
        assert result["source"] == "youtube"
        assert result["stats"]["drafted"] == 4
        mock_run.assert_called_once_with(
            source="youtube",
            filters={"query": "의료기기", "min_subscribers": 100000},
        )


# ---------------------------------------------------------------------------
# DB model: OutboundIntent
# ---------------------------------------------------------------------------

class TestOutboundIntentModel:
    def test_create_intent(self, db_session):
        intent = OutboundIntent(
            user_query="구독자 10만+ 의료기기 유튜브 채널",
            routed_source="youtube",
            routed_filters={"query": "의료기기", "min_subscribers": 100000},
            confidence=0.92,
            status="dispatched",
        )
        db_session.add(intent)
        db_session.commit()

        saved = db_session.query(OutboundIntent).first()
        assert saved is not None
        assert saved.user_query == "구독자 10만+ 의료기기 유튜브 채널"
        assert saved.routed_source == "youtube"
        assert saved.routed_filters["min_subscribers"] == 100000
        assert saved.confidence == 0.92
        assert saved.status == "dispatched"

    def test_default_status(self, db_session):
        intent = OutboundIntent(
            user_query="test",
            routed_source="youtube",
            confidence=0.5,
        )
        db_session.add(intent)
        db_session.commit()

        saved = db_session.query(OutboundIntent).first()
        assert saved.status == "pending_user_input"
