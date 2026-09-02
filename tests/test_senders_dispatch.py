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
    # 고른 발신 주소. 기본은 「안 골랐다」 — 그때는 스레드가 정합니다(이관 0105).
    msg.channel_account_id = overrides.get("channel_account_id", None)
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

    # 안 고르면 빈 문자열로 넘어갑니다 — 받는 쪽에서 「예전처럼」이라는 뜻입니다.
    client.find_conversation_reply_context.assert_awaited_once_with(
        "ticket-1", "to@example.com", preferred_account_id=""
    )
    client.send_conversation_message.assert_awaited_once()
    assert msg.hubspot_thread_id == "thread-1"
    assert msg.hubspot_message_id == "message-1"


@pytest.mark.asyncio
@patch("src.integrations.hubspot.HubSpotClient")
async def test_the_chosen_sending_address_reaches_the_route_lookup(mock_client_class) -> None:
    """운영자가 고른 발신 주소가 발송까지 살아서 갑니다 (이관 0105).

    행에 적힌 것이 그대로 `find_conversation_reply_context` 로 넘어가야 합니다 — 중간에
    떨어지면 화면에서는 고른 것처럼 보이는데 메일은 예전 주소로 나가고, **그건 나간 뒤에나
    알 수 있습니다.**
    """
    client = mock_client_class.return_value
    client.find_conversation_reply_context = AsyncMock(
        return_value=ConversationReplyContext("thread-1", "1002", "3114216464")
    )
    client.send_conversation_message = AsyncMock(return_value="message-1")
    client.close = AsyncMock()

    await send(_make_message(channel_account_id="3114216464"))

    client.find_conversation_reply_context.assert_awaited_once_with(
        "ticket-1", "to@example.com", preferred_account_id="3114216464"
    )
