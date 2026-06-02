"""Tests for outbound agent - dedup, scoring, drafting."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


from src.agents.outbound.agent import (
    DraftEmailResult,
    ICPScoreResult,
    OutboundAgent,
)
from src.agents.outbound.sources.base import ProspectCandidate
from src.db.models import Contact, Message, Prospect


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
    assert statuses["Good Lead"] == "analyzed"

    messages = verify.query(Message).all()
    assert len(messages) == 1
    assert messages[0].status == "pending_approval"
    assert messages[0].subject == "Hi"


def _run_single(db_session, candidate) -> dict:
    llm = MagicMock()
    llm.complete = MagicMock(
        side_effect=lambda *a, **k: ICPScoreResult(score=90, rationale="x")
    )
    with (
        patch("src.agents.outbound.agent.SessionLocal", return_value=db_session),
        patch("src.agents.outbound.agent.get_source", return_value=_stub_source([candidate])),
    ):
        return OutboundAgent(llm=llm).run("manual_csv")


def test_dedup_skips_never_contacted_prospect(db_session) -> None:
    """Existence-based: a prospect in the DB is skipped even if never contacted."""
    db_session.add(
        Prospect(
            source="manual_csv",
            normalized_email="never@acme.com",
            full_name="Never Contacted",
            status="skipped_lowscore",
            last_contacted_at=None,
        )
    )
    db_session.commit()

    stats = _run_single(
        db_session,
        ProspectCandidate(
            name="Never Contacted", email="never@acme.com", source="manual_csv"
        ),
    )
    assert stats["skipped_dup"] == 1
    assert stats["drafted"] == 0


def test_dedup_skips_existing_contact(db_session) -> None:
    """Existence-based: an email already known as a Contact is skipped."""
    db_session.add(
        Contact(
            email="known@corp.com",
            normalized_email="known@corp.com",
            full_name="Known Contact",
        )
    )
    db_session.commit()

    stats = _run_single(
        db_session,
        ProspectCandidate(name="Known Contact", email="known@corp.com", source="manual_csv"),
    )
    assert stats["skipped_dup"] == 1
    assert stats["drafted"] == 0


def test_dedup_normalizes_plus_and_case(db_session) -> None:
    """A plus-tag / different-case variant of an existing email is still a dup."""
    db_session.add(
        Prospect(
            source="manual_csv",
            normalized_email="lead@acme.com",
            full_name="Lead",
            status="analyzed",
        )
    )
    db_session.commit()

    stats = _run_single(
        db_session,
        ProspectCandidate(name="Lead", email="Lead+promo@Acme.com", source="manual_csv"),
    )
    assert stats["skipped_dup"] == 1


def test_rerun_does_not_crash_on_existing_email(db_session) -> None:
    """Running the same new candidate twice must not hit the UNIQUE constraint."""
    cand = ProspectCandidate(
        name="Fresh Lead", email="fresh@enterprise.kr", domain="enterprise.kr",
        country="korea", source="manual_csv",
    )

    def _llm():
        m = MagicMock()

        def se(prompt_name, variables=None, schema=None, **kw):
            if "icp_score" in prompt_name:
                return ICPScoreResult(score=90, rationale="fit", language_guess="ko")
            if "email" in prompt_name:
                return DraftEmailResult(subject="Hi", body="Hello", language="ko")
            return "ok"

        m.complete = MagicMock(side_effect=se)
        return m

    for expected_drafted, expected_dup in ((1, 0), (0, 1)):
        with (
            patch("src.agents.outbound.agent.SessionLocal", return_value=db_session),
            patch("src.agents.outbound.agent.get_source", return_value=_stub_source([cand])),
            patch("src.agents.outbound.enrichment.enrich_prospect", return_value={}),
        ):
            stats = OutboundAgent(llm=_llm()).run("manual_csv")
        assert stats["drafted"] == expected_drafted
        assert stats["skipped_dup"] == expected_dup


def test_manual_csv_uses_source_specific_prompt(db_session) -> None:
    """Verify the outbound agent picks email_manual_csv prompt, not generic."""
    candidates = [
        ProspectCandidate(
            name="CSV Lead",
            email="csv@target.kr",
            company="Target Co",
            domain="target.kr",
            country="korea",
            source="manual_csv",
            extra={"notes": "Spoke at PyCon Korea 2025"},
        ),
    ]

    llm = MagicMock()
    captured_prompts: list[str] = []

    def llm_side_effect(prompt_name, variables=None, schema=None, **kw):
        captured_prompts.append(prompt_name)
        if "icp_score" in prompt_name:
            return ICPScoreResult(score=80, rationale="Good fit", language_guess="ko")
        if "email" in prompt_name:
            return DraftEmailResult(subject="Hi CSV", body="Personalized.", language="ko")
        return "ok"

    llm.complete = MagicMock(side_effect=llm_side_effect)
    source = _stub_source(candidates)

    with (
        patch("src.agents.outbound.agent.SessionLocal", return_value=db_session),
        patch("src.agents.outbound.agent.get_source", return_value=source),
    ):
        agent = OutboundAgent(llm=llm)
        agent.run("manual_csv")

    email_prompts = [p for p in captured_prompts if "email" in p]
    assert email_prompts == ["outbound/email_manual_csv"]
