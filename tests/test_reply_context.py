"""Conversation context supplied to the customer-reply prompt."""

from __future__ import annotations

from unittest.mock import patch

from src.agents.inbound import InboundAgent
from src.db.models import Contact, Conversation, Message


def test_context_includes_summary_and_prior_turns_but_not_current(
    db_session_factory,
) -> None:
    with db_session_factory() as session:
        contact = Contact(
            normalized_email="context@example.com",
            email="context@example.com",
            full_name="Context User",
        )
        session.add(contact)
        session.flush()
        conversation = Conversation(
            contact_id=contact.id,
            summary="가격과 도입 일정을 논의 중입니다.",
            customer_requests="- 영어에서 스페인어 더빙\n- 6월 안에 PoC",
        )
        session.add(conversation)
        session.flush()
        session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    direction="outgoing",
                    channel="email",
                    subject="RE: Previous",
                    body="PoC 가능 여부를 확인하겠습니다.",
                    status="sent",
                ),
                Message(
                    conversation_id=conversation.id,
                    direction="inbound",
                    channel="email",
                    subject="RE: Previous",
                    body="Please confirm the PoC schedule.",
                    status="received",
                ),
            ]
        )
        session.commit()
        conversation_id = conversation.id

    agent = InboundAgent.__new__(InboundAgent)
    with patch("src.agents.inbound.SessionLocal", db_session_factory):
        context = agent._build_conversation_context(
            conversation_id, "Please confirm the PoC schedule."
        )

    assert "가격과 도입 일정을 논의 중입니다." in context
    assert "영어에서 스페인어 더빙" in context
    assert "PoC 가능 여부를 확인하겠습니다." in context
    assert "Please confirm the PoC schedule." not in context
