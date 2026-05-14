"""End-to-end smoke test - full pipeline with stubs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agents.inbound import ClassifyResult, DraftResult, InboundAgent, ScoreAdjustResult
from src.agents.approval import approve
from src.agents.report import ReportAgent
from src.db.base import Base
from src.db.models import Message


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session, Session
    session.close()


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


def test_full_pipeline(db) -> None:
    session, Session = db
    llm = _stub_llm()

    agent = InboundAgent(llm=llm, hubspot=None)
    with patch("src.agents.inbound.SessionLocal", return_value=session):
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

    verify = Session()
    msg = verify.get(Message, msg_id)
    assert msg.status == "pending_approval"

    with patch("src.agents.approval.SessionLocal", return_value=verify):
        approved = approve(msg_id, approver="slack:e2e-user")
    assert approved.status == "approved"

    report_session = Session()
    with (
        patch("src.agents.report.SessionLocal", return_value=report_session),
        patch.object(ReportAgent, "_save_report"),
    ):
        report_agent = ReportAgent(llm=llm)
        report = report_agent.generate("daily")

    assert "# Daily Report" in report
    assert "## Summary" in report
