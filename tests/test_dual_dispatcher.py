"""Tests for dual email+WhatsApp send dispatcher with DB tracking."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.db.models import Contact, Conversation, Message
from src.db.session import SessionLocal
from src.integrations.senders import send
from src.integrations.senders.whatsapp import WhatsAppSendError


def _create_test_message(phone: str = "+821012345678") -> int:
    """Create a test message in DB and return its ID.

    Email is stored on message.to_address (the primary channel); phone lives
    on the Contact row and the dispatcher reads it from there for the
    WhatsApp piggyback.
    """
    with SessionLocal() as session:
        contact = Contact(
            normalized_email="dual-test@example.com",
            email="dual-test@example.com",
            full_name="Dual Test",
            phone=phone,
            whatsapp_opt_in=True,
        )
        session.add(contact)
        session.flush()

        conv = Conversation(contact_id=contact.id, topic="test")
        session.add(conv)
        session.flush()

        msg = Message(
            conversation_id=conv.id,
            direction="outgoing",
            channel="email",
            to_address="dual-test@example.com",
            subject="Test",
            body="Test body",
        )
        session.add(msg)
        session.commit()
        return msg.id


def _get_message(msg_id: int) -> Message:
    with SessionLocal() as session:
        return session.get(Message, msg_id)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with SessionLocal() as session:
        for msg in session.query(Message).filter(Message.subject == "Test").all():
            session.delete(msg)
        for conv in session.query(Conversation).filter(Conversation.topic == "test").all():
            session.delete(conv)
        for c in session.query(Contact).filter(Contact.normalized_email == "dual-test@example.com").all():
            session.delete(c)
        session.commit()


# ---------- Schema check ----------


def test_whatsapp_columns_exist():
    """Messages table should have whatsapp tracking columns."""
    msg_id = _create_test_message()
    msg = _get_message(msg_id)
    assert msg.whatsapp_attempted is False
    assert msg.whatsapp_sent is False
    assert msg.whatsapp_error is None


# ---------- Both succeed ----------


@pytest.mark.asyncio
@patch("src.integrations.senders.send_whatsapp_template", new_callable=AsyncMock, return_value="wamid.ok")
@patch("src.integrations.senders.send_smtp")
async def test_email_and_whatsapp_both_succeed(mock_smtp, mock_wa):
    msg_id = _create_test_message()

    with SessionLocal() as session:
        msg = session.get(Message, msg_id)
        with patch("src.integrations.senders.settings") as mock_settings:
            mock_settings.EMAIL_PROVIDER = "smtp"
            mock_settings.SEND_OVERRIDE_EMAIL = ""
            mock_settings.WHATSAPP_ENABLED = True
            mock_settings.SMTP_USERNAME = "user"
            mock_settings.SMTP_PASSWORD = "pass"
            await send(msg)

    updated = _get_message(msg_id)
    assert updated.whatsapp_attempted is True
    assert updated.whatsapp_sent is True
    assert updated.whatsapp_error is None
    mock_smtp.assert_called_once()
    mock_wa.assert_called_once()


# ---------- Email succeeds, WhatsApp fails ----------


@pytest.mark.asyncio
@patch("src.integrations.senders.send_whatsapp_template", new_callable=AsyncMock, side_effect=WhatsAppSendError("API fail"))
@patch("src.integrations.senders.send_smtp")
async def test_email_succeeds_whatsapp_fails(mock_smtp, mock_wa):
    msg_id = _create_test_message()

    with SessionLocal() as session:
        msg = session.get(Message, msg_id)
        with patch("src.integrations.senders.settings") as mock_settings:
            mock_settings.EMAIL_PROVIDER = "smtp"
            mock_settings.SEND_OVERRIDE_EMAIL = ""
            mock_settings.WHATSAPP_ENABLED = True
            mock_settings.SMTP_USERNAME = "user"
            mock_settings.SMTP_PASSWORD = "pass"
            await send(msg)  # Should NOT raise

    updated = _get_message(msg_id)
    assert updated.whatsapp_attempted is True
    assert updated.whatsapp_sent is False
    assert "API fail" in updated.whatsapp_error


# ---------- WhatsApp disabled ----------


@pytest.mark.asyncio
@patch("src.integrations.senders.send_smtp")
async def test_whatsapp_disabled_skips(mock_smtp):
    msg_id = _create_test_message()

    with SessionLocal() as session:
        msg = session.get(Message, msg_id)
        with patch("src.integrations.senders.settings") as mock_settings:
            mock_settings.EMAIL_PROVIDER = "smtp"
            mock_settings.SEND_OVERRIDE_EMAIL = ""
            mock_settings.WHATSAPP_ENABLED = False
            mock_settings.SMTP_USERNAME = "user"
            mock_settings.SMTP_PASSWORD = "pass"
            await send(msg)

    updated = _get_message(msg_id)
    assert updated.whatsapp_attempted is False
    assert updated.whatsapp_sent is False


# ---------- Email fails → entire send fails ----------


@pytest.mark.asyncio
@patch("src.integrations.senders.send_smtp", side_effect=RuntimeError("SMTP down"))
async def test_email_failure_raises(mock_smtp):
    msg_id = _create_test_message()

    with SessionLocal() as session:
        msg = session.get(Message, msg_id)
        with patch("src.integrations.senders.settings") as mock_settings:
            mock_settings.EMAIL_PROVIDER = "smtp"
            mock_settings.SEND_OVERRIDE_EMAIL = ""
            mock_settings.WHATSAPP_ENABLED = True
            with pytest.raises(RuntimeError, match="SMTP down"):
                await send(msg)
