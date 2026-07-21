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
@patch("src.integrations.senders._log_hubspot_email", new_callable=AsyncMock)
@patch("src.integrations.senders.send_smtp")
async def test_smtp_sends_then_logs_to_hubspot(mock_smtp, mock_log) -> None:
    msg = _make_message()
    await send(msg)
    mock_smtp.assert_called_once_with(msg)
    mock_log.assert_awaited_once_with(msg)
