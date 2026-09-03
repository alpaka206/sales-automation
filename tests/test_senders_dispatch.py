"""Tests for the inbound reply sender dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.senders import send
from src.integrations.hubspot import ConversationReplyContext
from src.integrations.delivery import DeliveryPermanentError


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
    client.find_default_reply_context = AsyncMock(
        return_value=ConversationReplyContext("thread-1", "1002", "account-1")
    )
    client.find_conversation_reply_context = AsyncMock()
    client.send_conversation_message = AsyncMock(return_value="message-1")
    client.close = AsyncMock()
    msg = _make_message()

    await send(msg)

    # **안 골랐으면 「기본」 쪽으로 갑니다.** 그 함수 하나가 설정의 기본 발신 주소를 보고,
    # 못 쓰면 스레드로 물러섭니다 — 그리고 **고르개가 화면에 적는 「자동 — …」이 같은
    # 함수를 씁니다.** 예전에는 이 정책이 발송 경로에만 있어서, 화면은
    # 「support@perso.ai」라고 적는데 메일은 `perso.ai@estsoft.com` 으로 나갔습니다.
    client.find_default_reply_context.assert_awaited_once_with(
        "ticket-1", "to@example.com"
    )
    client.find_conversation_reply_context.assert_not_awaited()
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

@pytest.mark.asyncio
@patch("src.integrations.hubspot.HubSpotClient")
async def test_the_preferred_address_is_tried_across_inboxes_then_falls_back(
    mock_client_class, monkeypatch
) -> None:
    """**한 번 두드려 보고, 거절하면 원래 주소로 보냅니다** (2026-09-03 운영자 요청).

    「같은 인박스여야 한다」는 허브스팟의 규칙이 아니라 우리가 건 안전장치라, 폼으로 들어온
    문의는 기본 발신 주소로 **한 건도** 못 나갔습니다. 읽기로는 가릴 수 없으므로 발송이
    직접 답하게 합니다. 4xx 는 「거절이라 아무것도 안 나갔다」이므로 다시 보내도 고객이
    같은 메일을 두 번 받지 않습니다.
    """
    from src.common.config import settings
    monkeypatch.setattr(settings, "HUBSPOT_PREFERRED_EMAIL_CHANNEL_ACCOUNT_ID", "gtm")

    client = mock_client_class.return_value
    client.find_default_reply_context = AsyncMock(
        return_value=ConversationReplyContext("thread-1", "1002", "inbox-account")
    )
    client.close = AsyncMock()
    client.send_conversation_message = AsyncMock(
        side_effect=[DeliveryPermanentError("nope"), "message-1"]
    )

    msg = _make_message()
    await send(msg)

    calls = client.send_conversation_message.await_args_list
    assert len(calls) == 2
    assert calls[0].args[0] == ConversationReplyContext("thread-1", "1002", "gtm")
    # **스레드는 안 바뀝니다** — 계정만 바뀝니다.
    assert calls[1].args[0] == ConversationReplyContext("thread-1", "1002", "inbox-account")
    assert msg.hubspot_thread_id == "thread-1"


@pytest.mark.asyncio
@patch("src.integrations.hubspot.HubSpotClient")
async def test_an_unknown_outcome_is_never_retried_on_another_address(
    mock_client_class, monkeypatch
) -> None:
    """**이미 나갔을 수 있으면 다시 안 보냅니다.** 5xx·타임아웃이 그렇습니다.

    다시 보내면 고객이 같은 메일을 두 번 받고, 그건 되돌릴 수 없습니다.
    """
    from src.common.config import settings
    from src.integrations.delivery import DeliveryUnknown
    monkeypatch.setattr(settings, "HUBSPOT_PREFERRED_EMAIL_CHANNEL_ACCOUNT_ID", "gtm")

    client = mock_client_class.return_value
    client.find_default_reply_context = AsyncMock(
        return_value=ConversationReplyContext("thread-1", "1002", "inbox-account")
    )
    client.close = AsyncMock()
    client.send_conversation_message = AsyncMock(side_effect=DeliveryUnknown("timeout"))

    with pytest.raises(DeliveryUnknown):
        await send(_make_message())

    assert client.send_conversation_message.await_count == 1


@pytest.mark.asyncio
@patch("src.integrations.hubspot.HubSpotClient")
async def test_an_operator_choice_is_never_second_guessed(mock_client_class, monkeypatch) -> None:
    """운영자가 고른 주소에는 이 시도를 얹지 않습니다 — 고른 의미가 없어집니다."""
    from src.common.config import settings
    monkeypatch.setattr(settings, "HUBSPOT_PREFERRED_EMAIL_CHANNEL_ACCOUNT_ID", "gtm")

    client = mock_client_class.return_value
    client.find_conversation_reply_context = AsyncMock(
        return_value=ConversationReplyContext("thread-1", "1002", "picked")
    )
    client.find_default_reply_context = AsyncMock()
    client.close = AsyncMock()
    client.send_conversation_message = AsyncMock(return_value="message-1")

    await send(_make_message(channel_account_id="picked"))

    assert client.send_conversation_message.await_count == 1
    assert client.send_conversation_message.await_args.args[0].channel_account_id == "picked"
    client.find_default_reply_context.assert_not_awaited()
