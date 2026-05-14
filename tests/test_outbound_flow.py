"""Tests for outbound agent - dedup, scoring, drafting."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.agents.outbound.agent import (
    DraftEmailResult,
    ICPScoreResult,
    OutboundAgent,
)
from src.agents.outbound.sources.base import ProspectCandidate
from src.db.models import Message, Prospect


def _stub_source(candidates: list[ProspectCandidate]):
    source = MagicMock()
    source.name = "test"
    source.discover.return_value = candidates
    return source


def _mock_llm():
    llm = MagicMock()

    def side_effect(prompt_name, variables=None, schema=None, **kw):
        if "icp_score" in prompt_name:
            score = variables.get("_test_score", 70)
            return ICPScoreResult(
                score=score, rationale="Looks good", language_guess="ko"
            )
        if "email" in prompt_name:
            return DraftEmailResult(
                subject="Hello from us",
                body="We would love to connect.",
                language="ko",
            )
        return "ok"

    llm.complete = MagicMock(side_effect=side_effect)
    return llm


def test_outbound_flow_three_candidates(db_session, db_session_factory) -> None:
    existing = Prospect(
        source="manual_csv",
        normalized_email="dup@acme.com",
        full_name="Dup User",
        status="drafted",
        last_contacted_at=datetime.now(timezone.utc),
    )
    db_session.add(existing)
    db_session.commit()

    candidates = [
        ProspectCandidate(
            name="Dup User", email="dup@acme.com", company="Acme",
            source="manual_csv",
        ),
        ProspectCandidate(
            name="Low Score", email="low@small.com", company="Small Co",
            source="manual_csv",
        ),
        ProspectCandidate(
            name="Good Lead", email="good@enterprise.kr", company="Enterprise",
            domain="enterprise.kr", country="korea", source="manual_csv",
            extra={"notes": "Met at conference"},
        ),
    ]

    llm = MagicMock()

    def llm_side_effect(prompt_name, variables=None, schema=None, **kw):
        if "icp_score" in prompt_name:
            name = variables.get("full_name", "")
            if "Low" in name:
                return ICPScoreResult(score=20, rationale="Too small", language_guess="en")
            return ICPScoreResult(score=75, rationale="Good fit", language_guess="ko")
        if "email" in prompt_name:
            return DraftEmailResult(subject="Hi", body="Let's connect.", language="ko")
        return "ok"

    llm.complete = MagicMock(side_effect=llm_side_effect)

    source = _stub_source(candidates)

    with (
        patch("src.agents.outbound.agent.SessionLocal", return_value=db_session),
        patch("src.agents.outbound.agent.get_source", return_value=source),
    ):
        agent = OutboundAgent(llm=llm)
        stats = agent.run("manual_csv")

    assert stats["skipped_dup"] == 1
    assert stats["skipped_lowscore"] == 1
    assert stats["drafted"] == 1

    verify = db_session_factory()
    all_prospects = verify.query(Prospect).all()
    new_prospects = [p for p in all_prospects if p.id != existing.id]
    assert len(new_prospects) == 3

    statuses = {p.full_name: p.status for p in new_prospects}
    assert statuses["Dup User"] == "skipped_dup"
    assert statuses["Low Score"] == "skipped_lowscore"
    assert statuses["Good Lead"] == "drafted"

    messages = verify.query(Message).all()
    assert len(messages) == 1
    assert messages[0].status == "pending_approval"
    assert messages[0].subject == "Hi"
