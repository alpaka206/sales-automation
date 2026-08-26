"""HubSpot Conversations route selection and real-delivery contract tests."""

from __future__ import annotations

import json
from unittest.mock import patch

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


def test_a_validation_failure_names_the_field_not_just_multiple_errors():
    """HubSpot 400 의 진짜 이유는 ``errors`` 배열에 있습니다.

    ``message`` 는 어떤 원인이든 "Multiple errors validating request." 한 줄이라, 그것만
    남기면 로그가 무엇이 틀렸는지 말하지 않습니다. 실제로 발송 실패 하나를 그 로그만으로는
    진단할 수 없었습니다(msg 62, 2026-08-26).
    """
    import httpx

    from src.integrations.hubspot import HubSpotClient

    response = httpx.Response(
        400,
        json={
            "status": "error",
            "message": "Multiple errors validating request.",
            "errors": [
                {"message": "channelAccountId is not valid for this thread"},
                {"message": "recipients[0].actorId must be a visitor on this thread"},
            ],
        },
        request=httpx.Request("POST", "https://api.hubapi.com/x"),
    )
    error = HubSpotClient._lookup_error(response, "conversation message send")

    assert "Multiple errors validating request." in str(error)
    assert "channelAccountId is not valid for this thread" in str(error)
    assert "recipients[0].actorId must be a visitor on this thread" in str(error)


@respx.mock
@pytest.mark.asyncio
async def test_the_recipient_is_an_address_not_an_email_actor(client: HubSpotClient):
    """수신자에 ``actorId`` 를 넣지 않는다 — HubSpot 이 EMAIL actor 를 받는 쪽으로 거부한다.

    문서 예시에는 ``"actorId": "E-user@hubspot.com"`` 이 있고 actor 조회도 그 ID 를 200 으로
    돌려줍니다. 거부하는 곳은 발송 엔드포인트 하나입니다 — "Actor type EMAIL is not
    supported for receiving" (2026-08-26, msg 62). 그래서 읽기 검증으로는 못 잡고,
    이 테스트가 그 자리를 대신합니다.
    """
    respx.get(f"{BASE_URL}/conversations/v3/conversations/actors/A-1").mock(
        return_value=httpx.Response(200, json={"id": "A-1", "type": "AGENT"})
    )
    route = respx.post(
        f"{BASE_URL}/conversations/v3/conversations/threads/t1/messages"
    ).mock(return_value=httpx.Response(200, json={"id": "m1"}))

    with patch.object(settings, "HUBSPOT_SENDER_ACTOR_ID", "A-1"):
        await client.send_conversation_message(
            ConversationReplyContext("t1", "1002", "acct-1"),
            recipient_email="buyer@example.com",
            subject="s",
            text="t",
            rich_text="<p>t</p>",
        )

    sent = json.loads(route.calls.last.request.content)
    assert sent["recipients"] == [
        {
            "recipientField": "TO",
            "deliveryIdentifiers": [
                {"type": "HS_EMAIL_ADDRESS", "value": "buyer@example.com"}
            ],
        }
    ]
    assert "actorId" not in sent["recipients"][0]
