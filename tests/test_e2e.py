"""End-to-end smoke test - full pipeline with stubs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from src.agents.inbound import ClassifyResult, DraftResult, InboundAgent, ScoreAdjustResult
from src.agents.approval import approve
from src.agents.report import ReportAgent
from src.db.models import Message


def _stub_llm():
    llm = MagicMock()

    def side_effect(prompt_name, variables=None, schema=None, **kw):
        if "classify" in prompt_name:
            return ClassifyResult(category="purchase_inquiry", reasoning="Wants to buy")
        if "score_adjust" in prompt_name:
            return ScoreAdjustResult(adjustment=5, reasoning="Good fit")
        if "draft_reply" in prompt_name:
            return DraftResult(
                subject="Re: Your Inquiry",
                body="Thank you for reaching out.",
                language="ko",
            )
        if "report" in prompt_name:
            return "Today was productive."
        return "ok"

    llm.complete = MagicMock(side_effect=side_effect)
    return llm


def test_full_pipeline(db_session, db_session_factory) -> None:
    llm = _stub_llm()

    agent = InboundAgent(llm=llm, hubspot=None)
    with patch("src.agents.inbound.SessionLocal", return_value=db_session):
        result = agent.handle({
            "object_id": "e2e-001",
            "occurred_at": "2026-05-14T12:00:00Z",
            "email": "buyer@enterprise.kr",
            "full_name": "E2E Buyer",
            "company": "Enterprise Corp",
            "country": "korea",
            "last_message": "We need your product ASAP.",
        })

    assert result is not None
    assert result["category"] == "purchase_inquiry"
    msg_id = result["message_id"]

    verify = db_session_factory()
    msg = verify.get(Message, msg_id)
    assert msg.status == "pending_approval"

    # The review-screen translation step persists the target language before approval.
    # This stub already returned English text, so simulate the button's no-op branch.
    msg.language = msg.target_language
    verify.commit()

    with patch("src.agents.approval.SessionLocal", return_value=verify):
        approved = approve(msg_id, approver="slack:e2e-user")
    assert approved.status == "approved"

    report_session = db_session_factory()
    with (
        patch("src.agents.report.SessionLocal", return_value=report_session),
        patch.object(ReportAgent, "_save_report"),
    ):
        report_agent = ReportAgent(llm=llm)
        report = report_agent.generate("daily")

    assert "# Daily Report" in report
    assert "## Summary" in report
