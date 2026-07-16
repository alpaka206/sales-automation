"""Tests for the inbound reply sender dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.senders import send


def _make_message(**overrides) -> MagicMock:
    msg = MagicMock()
    msg.id = overrides.get("id", 1)
    msg.channel = overrides.get("channel", "email")
    msg.direction = overrides.get("direction", "outgoing")
    msg.to_address = overrides.get("to_address", "to@example.com")
    msg.subject = overrides.get("subject", "Test")
    msg.body = overrides.get("body", "Hello")
    msg.language = overrides.get("language", "ko")
    msg.target_language = overrides.get("target_language", None)
    msg.prompt_variant = overrides.get("prompt_variant", "auto_ack")
    msg.signature_key = overrides.get("signature_key", "none")
    msg.conversation = MagicMock()
    msg.conversation.contact_id = overrides.get("contact_id", 100)
    msg.conversation.contact = None
    return msg


@pytest.mark.asyncio
@patch("src.integrations.senders.send_whatsapp", new_callable=AsyncMock)
async def test_whatsapp_channel_sends_directly(mock_wa) -> None:
    msg = _make_message(channel="whatsapp")
    with patch("src.integrations.senders.settings") as configured:
        configured.SEND_OVERRIDE_EMAIL = ""
        await send(msg)
    mock_wa.assert_awaited_once_with(msg)


@pytest.mark.asyncio
@patch("src.integrations.senders._log_hubspot_email", new_callable=AsyncMock)
@patch("src.integrations.senders.send_smtp")
async def test_smtp_sends_then_logs_to_hubspot(mock_smtp, mock_log) -> None:
    msg = _make_message()
    with patch("src.integrations.senders.settings") as configured:
        configured.EMAIL_PROVIDER = "smtp"
        configured.WHATSAPP_ENABLED = False
        configured.SEND_OVERRIDE_EMAIL = ""
        await send(msg)
    mock_smtp.assert_called_once_with(msg)
    mock_log.assert_awaited_once_with(msg)


@pytest.mark.asyncio
@patch("src.integrations.senders.send_smtp")
async def test_hubspot_provider_is_rejected(mock_smtp) -> None:
    msg = _make_message()
    with patch("src.integrations.senders.settings") as configured:
        configured.EMAIL_PROVIDER = "hubspot"
        configured.WHATSAPP_ENABLED = False
        configured.SEND_OVERRIDE_EMAIL = ""
        with pytest.raises(RuntimeError, match="cannot deliver mail"):
            await send(msg)
    mock_smtp.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_provider_raises() -> None:
    msg = _make_message()
    with patch("src.integrations.senders.settings") as configured:
        configured.EMAIL_PROVIDER = "carrier_pigeon"
        configured.WHATSAPP_ENABLED = False
        configured.SEND_OVERRIDE_EMAIL = ""
        with pytest.raises(ValueError, match="Unknown EMAIL_PROVIDER"):
            await send(msg)
