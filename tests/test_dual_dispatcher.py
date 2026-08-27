"""Tests for the HubSpot Conversations send dispatcher with DB tracking."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.db.models import Contact, Conversation, Message
from src.db.session import SessionLocal
from src.integrations.senders import send
from src.integrations.delivery import DeliveryPermanentError


def _create_test_message(phone: str = "+821012345678") -> int:
    """Create a test message in DB and return its ID."""
    with SessionLocal() as session:
        contact = Contact(
            normalized_email="dual-test@example.com",
            email="dual-test@example.com",
            full_name="Dual Test",
            phone=phone,
        )
        session.add(contact)
        session.flush()

        conv = Conversation(
            contact_id=contact.id,
            inquiry_subject="test",
            hubspot_ticket_id="ticket-test",
        )
        session.add(conv)
        session.flush()

        msg = Message(
            conversation_id=conv.id,
            direction="outgoing",
            to_address="dual-test@example.com",
            subject="Test",
            body="Test body",
        )
        session.add(msg)
        session.commit()
        return msg.id


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with SessionLocal() as session:
        for msg in session.query(Message).filter(Message.subject == "Test").all():
            session.delete(msg)
        for conv in session.query(Conversation).filter(Conversation.inquiry_subject == "test").all():
            session.delete(conv)
        for c in session.query(Contact).filter(Contact.normalized_email == "dual-test@example.com").all():
            session.delete(c)
        session.commit()


@pytest.mark.asyncio
@patch(
    "src.integrations.hubspot.HubSpotClient",
    side_effect=DeliveryPermanentError("HubSpot unavailable"),
)
async def test_email_failure_raises(mock_client):
    msg_id = _create_test_message()

    with SessionLocal() as session:
        msg = session.get(Message, msg_id)
        with pytest.raises(DeliveryPermanentError, match="HubSpot unavailable"):
            await send(msg)
