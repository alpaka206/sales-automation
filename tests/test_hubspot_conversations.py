"""HubSpot Conversations route selection and real-delivery contract tests."""

from __future__ import annotations

import httpx
import pytest
import respx

from src.common.config import settings
from src.integrations.delivery import DeliveryPermanentError, DeliveryUnknown
from src.integrations.hubspot import BASE_URL, ConversationReplyContext, HubSpotClient


@pytest.fixture()
def client() -> HubSpotClient:
    return HubSpotClient(token="test-token")


def _party(email: str) -> dict:
    return {
        "deliveryIdentifiers": [{"type": "HS_EMAIL_ADDRESS", "value": email}]
    }


@respx.mock
@pytest.mark.asyncio
async def test_selects_latest_matching_email_route(client: HubSpotClient) -> None:
    respx.get(f"{BASE_URL}/conversations/v3/conversations/threads").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"id": "older", "inboxId": "inbox-1"},
                    {"id": "newer", "inboxId": "inbox-2"},
                ]
            },
        )
    )
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/threads/older/messages"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "type": "MESSAGE",
                        "createdAt": "2026-01-01T00:00:00Z",
                        "channelId": "1002",
                        "channelAccountId": "account-old",
                        "recipients": [_party("buyer@example.com")],
                    }
                ]
            },
        )
    )
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/threads/newer/messages"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "type": "MESSAGE",
                        "createdAt": "2026-02-01T00:00:00Z",
                        "channelId": "1002",
                        "channelAccountId": "account-new",
                        "senders": [_party("buyer@example.com")],
                    }
                ]
            },
        )
    )
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/channel-accounts/account-new"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "account-new",
                "channelId": "1002",
                "active": True,
                "authorized": True,
                "archived": False,
            },
        )
    )

    context = await client.find_conversation_reply_context("ticket-1", "buyer@example.com")
    await client.close()

    assert context == ConversationReplyContext("newer", "1002", "account-new")


@respx.mock
@pytest.mark.asyncio
async def test_form_only_thread_uses_same_inbox_fallback(
    client: HubSpotClient, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "HUBSPOT_DEFAULT_EMAIL_CHANNEL_ACCOUNT_ID", "support")
    respx.get(f"{BASE_URL}/conversations/v3/conversations/threads").mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": "form-thread", "inboxId": "b2b-inbox"}]}
        )
    )
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/threads/form-thread/messages"
    ).mock(return_value=httpx.Response(200, json={"results": []}))
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/channel-accounts/support"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "channelId": "1002",
                "inboxId": "b2b-inbox",
                "active": True,
                "authorized": True,
                "archived": False,
            },
        )
    )

    context = await client.find_conversation_reply_context("ticket-1", "buyer@example.com")
    await client.close()

    assert context == ConversationReplyContext("form-thread", "1002", "support")


@respx.mock
@pytest.mark.asyncio
async def test_form_only_thread_rejects_cross_inbox_account(
    client: HubSpotClient, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "HUBSPOT_DEFAULT_EMAIL_CHANNEL_ACCOUNT_ID", "wrong")
    respx.get(f"{BASE_URL}/conversations/v3/conversations/threads").mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": "form-thread", "inboxId": "b2b-inbox"}]}
        )
    )
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/threads/form-thread/messages"
    ).mock(return_value=httpx.Response(200, json={"results": []}))
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/channel-accounts/wrong"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "channelId": "1002",
                "inboxId": "other-inbox",
                "active": True,
                "authorized": True,
                "archived": False,
            },
        )
    )

    with pytest.raises(DeliveryPermanentError, match="does not belong"):
        await client.find_conversation_reply_context("ticket-1", "buyer@example.com")
    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_send_uses_agent_and_delivery_identifiers(
    client: HubSpotClient, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "HUBSPOT_SENDER_ACTOR_ID", "A-82843387")
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/actors/A-82843387"
    ).mock(
        return_value=httpx.Response(
            200, json={"id": "A-82843387", "type": "AGENT"}
        )
    )
    route = respx.post(
        f"{BASE_URL}/conversations/v3/conversations/threads/thread-1/messages"
    ).mock(return_value=httpx.Response(201, json={"id": "message-1"}))

    result = await client.send_conversation_message(
        ConversationReplyContext("thread-1", "1002", "account-1"),
        recipient_email="buyer@example.com",
        subject="Re: Inquiry",
        text="Hello",
        rich_text="<p>Hello</p>",
    )
    await client.close()

    assert result == "message-1"
    payload = route.calls[0].request.content.decode()
    assert '"senderActorId":"A-82843387"' in payload
    assert '"channelAccountId":"account-1"' in payload
    assert '"deliveryIdentifiers":[{"type":"HS_EMAIL_ADDRESS"' in payload


@respx.mock
@pytest.mark.asyncio
async def test_send_5xx_is_delivery_unknown_without_retry(
    client: HubSpotClient, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "HUBSPOT_SENDER_ACTOR_ID", "A-82843387")
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/actors/A-82843387"
    ).mock(
        return_value=httpx.Response(
            200, json={"id": "A-82843387", "type": "AGENT"}
        )
    )
    route = respx.post(
        f"{BASE_URL}/conversations/v3/conversations/threads/thread-1/messages"
    ).mock(return_value=httpx.Response(503, json={"message": "unavailable"}))

    with pytest.raises(DeliveryUnknown):
        await client.send_conversation_message(
            ConversationReplyContext("thread-1", "1002", "account-1"),
            recipient_email="buyer@example.com",
            subject="Re: Inquiry",
            text="Hello",
            rich_text="<p>Hello</p>",
        )
    await client.close()

    assert route.call_count == 1
