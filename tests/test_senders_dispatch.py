"""Tests for the inbound reply sender dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.senders import send
from src.integrations.hubspot import ConversationReplyContext


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
    msg.prompt_variant = overrides.get("prompt_variant", None)
    msg.signature_key = overrides.get("signature_key", "none")
    msg.conversation = MagicMock()
    msg.conversation.contact_id = overrides.get("contact_id", 100)
    msg.conversation.hubspot_ticket_id = overrides.get("ticket_id", "ticket-1")
    msg.conversation.contact = None
    return msg


@pytest.mark.asyncio
@patch("src.integrations.hubspot.HubSpotClient")
async def test_sends_on_existing_hubspot_thread(mock_client_class) -> None:
    client = mock_client_class.return_value
    client.find_conversation_reply_context = AsyncMock(
        return_value=ConversationReplyContext("thread-1", "1002", "account-1")
    )
    client.send_conversation_message = AsyncMock(return_value="message-1")
    client.close = AsyncMock()
    msg = _make_message()

    await send(msg)

    client.find_conversation_reply_context.assert_awaited_once_with(
        "ticket-1", "to@example.com"
    )
    client.send_conversation_message.assert_awaited_once()
    assert msg.hubspot_thread_id == "thread-1"
    assert msg.hubspot_message_id == "message-1"
