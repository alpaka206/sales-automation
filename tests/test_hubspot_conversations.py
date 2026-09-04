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


# --------------------------------------------------------------------------- #
# 운영자가 고른 발신 주소 (이관 0105)
# --------------------------------------------------------------------------- #
def _thread_with_one_email(thread_id: str, inbox: str, account: str) -> None:
    """스레드 하나 + 그 안의 이메일 한 통 + 그 계정 조회를 깔아 둡니다."""
    respx.get(f"{BASE_URL}/conversations/v3/conversations/threads").mock(
        return_value=httpx.Response(200, json={"results": [{"id": thread_id, "inboxId": inbox}]})
    )
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/threads/{thread_id}/messages"
    ).mock(
        return_value=httpx.Response(200, json={"results": [{
            "type": "MESSAGE", "createdAt": "2026-02-01T00:00:00Z",
            "channelId": "1002", "channelAccountId": account,
            "senders": [_party("buyer@example.com")],
        }]})
    )
    respx.get(f"{BASE_URL}/conversations/v3/conversations/threads/{thread_id}").mock(
        return_value=httpx.Response(200, json={"id": thread_id, "inboxId": inbox})
    )
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/channel-accounts/{account}"
    ).mock(
        return_value=httpx.Response(200, json={
            "id": account, "channelId": "1002", "inboxId": inbox,
            "active": True, "authorized": True, "archived": False,
        })
    )


def _account(account: str, inbox: str, **over) -> None:
    body = {"id": account, "channelId": "1002", "inboxId": inbox,
            "active": True, "authorized": True, "archived": False}
    body.update(over)
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/channel-accounts/{account}"
    ).mock(return_value=httpx.Response(200, json=body))


@respx.mock
@pytest.mark.asyncio
async def test_the_operator_can_choose_the_sending_address(client: HubSpotClient) -> None:
    """고른 계정이 스레드의 것을 이깁니다 — 그게 이 기능의 전부입니다 (2026-09-02 지시).

    **스레드는 안 바뀝니다.** 어느 스레드에 답을 붙일지는 운영자가 판단할 일이 아니고,
    엉뚱한 스레드에 붙으면 고객이 보는 대화가 두 갈래가 됩니다.
    """
    _thread_with_one_email("t1", "inbox-1", "team-account")
    _account("personal-account", "inbox-1")

    context = await client.find_conversation_reply_context(
        "ticket-1", "buyer@example.com", preferred_account_id="personal-account"
    )
    assert context == ConversationReplyContext("t1", "1002", "personal-account")


@respx.mock
@pytest.mark.asyncio
async def test_nothing_chosen_keeps_the_old_behaviour(client: HubSpotClient) -> None:
    """안 고르면 예전 그대로 — 스레드에 있던 계정입니다. 손대지 않은 초안이 그렇습니다."""
    _thread_with_one_email("t1", "inbox-1", "team-account")

    context = await client.find_conversation_reply_context("ticket-1", "buyer@example.com")
    assert context.channel_account_id == "team-account"


@respx.mock
@pytest.mark.asyncio
async def test_a_sender_from_another_inbox_is_allowed(client: HubSpotClient) -> None:
    """**다른 인박스의 주소도 씁니다 — 실발송으로 확인했습니다** (2026-09-03 운영자 실측).

    오래 「같은 인박스여야 한다」로 막아 두었습니다. 근거는 허브스팟 답장창 드롭다운이 그
    대화의 인박스 계정만 보여 준다는 **화면 관찰**이었는데, 화면이 안 보여 주는 것과 API 가
    거절하는 것은 다른 이야기였습니다. 폼으로 들어온(=`Inbox`) 티켓에
    `perso.ai@estsoft.com`(GTM Marketing)으로 보냈고 **그대로 나갔습니다.**

    스레드는 이 고객과 가장 최근에 메일이 오간 자리 그대로이고, 계정만 바뀝니다.
    """
    _thread_with_one_email("t1", "inbox-1", "team-account")
    _account("other-inbox-account", "inbox-2")

    context = await client.find_conversation_reply_context(
        "ticket-1", "buyer@example.com", preferred_account_id="other-inbox-account"
    )

    assert context.channel_account_id == "other-inbox-account"
    assert context.thread_id == "t1", "붙는 자리는 안 바뀝니다 — 계정만 바뀝니다"


@respx.mock
@pytest.mark.asyncio
async def test_a_dead_chosen_sender_fails_instead_of_falling_back(client: HubSpotClient) -> None:
    """고른 계정이 못 쓰는 것이면 **조용히 다른 주소로 나가지 않습니다.**

    운영자가 「이 주소로 보낸다」고 고른 것이라, 다른 주소로 나가면 고른 의미가 없습니다 —
    그리고 나간 뒤에는 되돌릴 수 없습니다.
    """
    _thread_with_one_email("t1", "inbox-1", "team-account")
    _account("revoked-account", "inbox-1", authorized=False)

    with pytest.raises(DeliveryPermanentError):
        await client.find_conversation_reply_context(
            "ticket-1", "buyer@example.com", preferred_account_id="revoked-account"
        )


@respx.mock
@pytest.mark.asyncio
async def test_the_picker_offers_only_what_the_operator_allowed(
    client: HubSpotClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**고르개에 뜰 주소는 운영자가 정합니다**(`HUBSPOT_REPLY_SENDER_ACCOUNT_IDS`).

    포털에 연결된 주소가 곧 「우리가 쓰는 주소」는 아닙니다 — `support@perso.ai` 는 연결돼
    있지만 이 팀이 안 씁니다(2026-09-03 운영자). 목록에 두면 언젠가 골라지고, 그건 고객이
    받는 메일의 보낸사람이 바뀐다는 뜻입니다. 주소를 코드에 박지 않는 이유는 늘 같습니다 —
    포털 설정이 바뀌면 코드가 조용히 틀려집니다.

    인박스는 더 이상 울타리가 아닙니다(실발송으로 확인). 그래서 `inbox-9` 의 주소도
    허락하면 뜹니다.
    """
    monkeypatch.setattr(
        settings, "HUBSPOT_REPLY_SENDER_ACCOUNT_IDS", "team-account, elsewhere"
    )
    _thread_with_one_email("t1", "inbox-1", "team-account")
    respx.get(f"{BASE_URL}/conversations/v3/conversations/channel-accounts").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "team-account", "channelId": "1002", "inboxId": "inbox-1",
             "active": True, "authorized": True, "archived": False,
             "deliveryIdentifier": {"value": "perso.ai@estsoft.com"}},
            {"id": "personal-account", "channelId": "1002", "inboxId": "inbox-1",
             "active": True, "authorized": True, "archived": False,
             "deliveryIdentifier": {"value": "untae@estsoft.com"}},
            # 다른 인박스 — 고를 수 없어야 합니다.
            {"id": "elsewhere", "channelId": "1002", "inboxId": "inbox-9",
             "active": True, "authorized": True, "archived": False,
             "deliveryIdentifier": {"value": "support@perso.ai"}},
            # 연결이 끊긴 계정 — 목록에 두면 고른 순간 발송이 죽습니다.
            {"id": "revoked", "channelId": "1002", "inboxId": "inbox-1",
             "active": True, "authorized": False, "archived": False,
             "deliveryIdentifier": {"value": "old@estsoft.com"}},
            # 폼 채널 — 이메일이 아닙니다.
            {"id": "a-form", "channelId": "1003", "inboxId": "inbox-1",
             "active": True, "authorized": True, "archived": False},
        ]})
    )

    found = await client.list_reply_senders("ticket-1", "buyer@example.com")
    senders = found["senders"]

    # 허락한 둘만 뜹니다 — `personal-account`(허락 안 함)와 `revoked`(연결 끊김)는 빠지고,
    # 다른 인박스의 `elsewhere` 는 이제 막히지 않으므로 뜹니다.
    assert [s["address"] for s in senders] == ["perso.ai@estsoft.com", "support@perso.ai"]
    # 아무것도 안 골랐을 때 나갈 주소가 맨 앞에 오고, 그렇다고 표시됩니다.
    assert senders[0]["is_default"] is True
    assert senders[1]["is_default"] is False


@respx.mock
@pytest.mark.asyncio
async def test_the_picker_default_is_what_the_send_would_actually_use(
    client: HubSpotClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**화면에 적힌 「자동 — …」이 실제로 나갈 주소여야 합니다** (2026-09-03).

    고르개가 `find_conversation_reply_context` 를 **기본 발신 주소 없이** 부르고 있었습니다.
    발송 경로는 넣고 부르므로 둘의 답이 갈렸고, 화면은 「자동 — support@perso.ai」라고 적는데
    메일은 `perso.ai@estsoft.com` 으로 나갔습니다 — 운영 실측으로 티켓 48건 중 41건입니다.
    나간 뒤에나 알 수 있는 종류의 어긋남이라 여기서 고정합니다.

    **목록도 한 인박스에 갇히면 안 됩니다.** 티켓 하나가 인박스 여러 곳에 스레드를 갖는
    일이 흔한데(폼은 `Inbox`, 메일은 `GTM Marketing`), 기본값이 정해진 스레드의 인박스만
    보면 나머지가 통째로 사라져 「원래 오던 주소」를 고를 길이 없어집니다.
    """
    monkeypatch.setattr(
        settings, "HUBSPOT_PREFERRED_EMAIL_CHANNEL_ACCOUNT_ID", "gtm-account"
    )
    respx.get(f"{BASE_URL}/conversations/v3/conversations/threads").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "t-inbox", "inboxId": "inbox-1"},
            {"id": "t-gtm", "inboxId": "inbox-gtm"},
        ]})
    )
    for thread_id, account in (("t-inbox", "support-account"), ("t-gtm", "gtm-account")):
        respx.get(
            f"{BASE_URL}/conversations/v3/conversations/threads/{thread_id}/messages"
        ).mock(
            return_value=httpx.Response(200, json={"results": [{
                "type": "MESSAGE", "createdAt": "2026-02-01T00:00:00Z",
                "channelId": "1002", "channelAccountId": account,
                "senders": [_party("buyer@example.com")],
            }]})
        )
    respx.get(f"{BASE_URL}/conversations/v3/conversations/channel-accounts").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "support-account", "channelId": "1002", "inboxId": "inbox-1",
             "active": True, "authorized": True, "archived": False,
             "deliveryIdentifier": {"value": "support@perso.ai"}},
            {"id": "gtm-account", "channelId": "1002", "inboxId": "inbox-gtm",
             "active": True, "authorized": True, "archived": False,
             "deliveryIdentifier": {"value": "perso.ai@estsoft.com"}},
        ]})
    )
    for account, inbox in (("support-account", "inbox-1"), ("gtm-account", "inbox-gtm")):
        respx.get(
            f"{BASE_URL}/conversations/v3/conversations/channel-accounts/{account}"
        ).mock(
            return_value=httpx.Response(200, json={
                "id": account, "channelId": "1002", "inboxId": inbox,
                "active": True, "authorized": True, "archived": False,
            })
        )

    found = await client.list_reply_senders("ticket-1", "buyer@example.com")
    senders = found["senders"]

    default = next(x for x in senders if x["is_default"])
    assert default["address"] == "perso.ai@estsoft.com"
    # 그리고 실제 발송이 고르는 것과 **같아야** 합니다.
    context = await client.find_default_reply_context("ticket-1", "buyer@example.com")
    assert context.channel_account_id == default["id"]
    # 원래 오던 주소도 여전히 고를 수 있습니다 — 인박스가 다르다고 사라지지 않습니다.
    assert {x["address"] for x in senders} == {
        "perso.ai@estsoft.com", "support@perso.ai",
    }


@respx.mock
@pytest.mark.asyncio
async def test_the_chosen_sender_picks_a_thread_in_its_own_inbox(client: HubSpotClient) -> None:
    """**계정이 먼저이고 스레드가 나중입니다** (2026-09-02 운영자 지시).

    아무도 안 골랐을 때는 「가장 최근에 오간 스레드」가 이기고 계정은 그 스레드가 정합니다.
    골랐을 때 같은 규칙을 쓰면, 고른 주소가 그 스레드의 인박스에 없다는 이유로 **늘**
    거절됩니다 — 운영 실측으로 B2B 티켓의 32%만 `perso.ai@estsoft.com` 으로 나갔습니다.
    그래서 고른 주소가 있으면 그 주소를 쓸 수 있는 스레드를 찾습니다.
    """
    respx.get(f"{BASE_URL}/conversations/v3/conversations/threads").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "gtm", "inboxId": "inbox-gtm"},
            {"id": "support", "inboxId": "inbox-support"},
        ]})
    )
    # 가장 최근 메일은 support 스레드에 있습니다 — 안 골랐으면 이쪽이 이깁니다.
    respx.get(f"{BASE_URL}/conversations/v3/conversations/threads/gtm/messages").mock(
        return_value=httpx.Response(200, json={"results": [{
            "type": "MESSAGE", "createdAt": "2026-01-01T00:00:00Z",
            "channelId": "1002", "channelAccountId": "gtm-account",
            "senders": [_party("buyer@example.com")],
        }]})
    )
    respx.get(f"{BASE_URL}/conversations/v3/conversations/threads/support/messages").mock(
        return_value=httpx.Response(200, json={"results": [{
            "type": "MESSAGE", "createdAt": "2026-06-01T00:00:00Z",
            "channelId": "1002", "channelAccountId": "support-account",
            "senders": [_party("buyer@example.com")],
        }]})
    )
    _account("gtm-account", "inbox-gtm")
    _account("support-account", "inbox-support")

    # 안 고르면 최신 스레드(support).
    plain = await client.find_conversation_reply_context("ticket-1", "buyer@example.com")
    assert plain == ConversationReplyContext("support", "1002", "support-account")

    # 고르면 그 주소가 있는 스레드(gtm) — 더 오래된 스레드여도.
    chosen = await client.find_conversation_reply_context(
        "ticket-1", "buyer@example.com", preferred_account_id="gtm-account"
    )
    assert chosen == ConversationReplyContext("gtm", "1002", "gtm-account")


@respx.mock
@pytest.mark.asyncio
async def test_a_form_only_thread_in_that_inbox_is_usable_when_it_is_the_only_one(
    client: HubSpotClient,
) -> None:
    """그 인박스에 메일이 오간 적이 없어도, 스레드가 하나뿐이면 거기로 답합니다.

    폼·챗봇으로만 들어온 스레드가 이 경우입니다 — 운영 실측 50건 중 14건이 그랬고, 이걸
    못 쓰면 그 티켓들은 고른 주소로 영영 답할 수 없습니다.
    """
    respx.get(f"{BASE_URL}/conversations/v3/conversations/threads").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "gtm-form", "inboxId": "inbox-gtm"},
            {"id": "support", "inboxId": "inbox-support"},
        ]})
    )
    respx.get(f"{BASE_URL}/conversations/v3/conversations/threads/gtm-form/messages").mock(
        # 폼으로 들어온 스레드 — 이메일(1002) 메시지가 없습니다.
        return_value=httpx.Response(200, json={"results": [{
            "type": "MESSAGE", "createdAt": "2026-05-01T00:00:00Z",
            "channelId": "1003", "channelAccountId": "a-form",
            "senders": [_party("buyer@example.com")],
        }]})
    )
    respx.get(f"{BASE_URL}/conversations/v3/conversations/threads/support/messages").mock(
        return_value=httpx.Response(200, json={"results": [{
            "type": "MESSAGE", "createdAt": "2026-06-01T00:00:00Z",
            "channelId": "1002", "channelAccountId": "support-account",
            "senders": [_party("buyer@example.com")],
        }]})
    )
    _account("gtm-account", "inbox-gtm")

    chosen = await client.find_conversation_reply_context(
        "ticket-1", "buyer@example.com", preferred_account_id="gtm-account"
    )
    assert chosen == ConversationReplyContext("gtm-form", "1002", "gtm-account")


@respx.mock
@pytest.mark.asyncio
async def test_a_sender_with_no_conversation_in_its_inbox_still_sends(
    client: HubSpotClient,
) -> None:
    """그 주소의 인박스에 이 티켓의 대화가 없어도 **나갑니다** (2026-09-03 실발송 확인).

    이것이 운영에서 가장 흔한 모양입니다 — 폼으로만 들어온 티켓 93건(전수 328건 중)이 전부
    이 경우이고, 예전 규칙에서는 그 전부가 `perso.ai@estsoft.com` 으로 **한 건도** 못
    나갔습니다. 붙는 자리는 이 고객과 메일이 오간 스레드 그대로입니다.
    """
    _thread_with_one_email("t1", "inbox-support", "support-account")
    _account("gtm-account", "inbox-gtm")

    context = await client.find_conversation_reply_context(
        "ticket-1", "buyer@example.com", preferred_account_id="gtm-account"
    )

    assert context.channel_account_id == "gtm-account"
    assert context.thread_id == "t1"


@respx.mock
@pytest.mark.asyncio
async def test_a_form_thread_does_not_reply_from_the_machine_address(
    client: HubSpotClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**기계 주소는 맨 뒤로 미룹니다** (2026-09-03 운영자 보고).

    폼으로 들어온 문의의 첫 메일은 허브스팟이 자동 발급한 주소
    (`support@45169260.hubspot-inbox.com`)로 기록됩니다 — 폼 채널에 「Customer agent reply
    email」이 설정돼 있지 않아서입니다. 가장 최근 후보를 그대로 고르면 **고객이 받는 메일의
    보낸사람이 그 기계 주소**가 됩니다. 실측 103건 중 2건이 그 상태였습니다.

    설정의 기본 발신 주소가 같은 인박스에 있으면 그쪽으로 갑니다.
    """
    monkeypatch.setattr(
        settings, "HUBSPOT_DEFAULT_EMAIL_CHANNEL_ACCOUNT_ID", "team-account"
    )
    monkeypatch.setattr(settings, "HUBSPOT_PREFERRED_EMAIL_CHANNEL_ACCOUNT_ID", "")
    _thread_with_one_email("t1", "inbox-1", "relay-1")
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/channel-accounts/relay-1"
    ).mock(
        return_value=httpx.Response(200, json={
            "id": "relay-1", "channelId": "1002", "inboxId": "inbox-1",
            "active": True, "authorized": True, "archived": False,
            "deliveryIdentifier": {"value": "support@45169260.hubspot-inbox.com"},
        })
    )
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/channel-accounts/team-account"
    ).mock(
        return_value=httpx.Response(200, json={
            "id": "team-account", "channelId": "1002", "inboxId": "inbox-1",
            "active": True, "authorized": True, "archived": False,
            "deliveryIdentifier": {"value": "support@perso.ai"},
        })
    )

    context = await client.find_default_reply_context("ticket-1", "buyer@example.com")

    assert context.channel_account_id == "team-account"


@respx.mock
@pytest.mark.asyncio
async def test_the_machine_address_is_still_used_rather_than_failing(
    client: HubSpotClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**못 보내는 것이 더 나쁩니다.** 사람이 쓰는 주소가 하나도 없으면 기계 주소로 갑니다.

    설정의 기본 발신 주소가 **다른 인박스**라 쓸 수 없는 상황입니다. 여기서 실패로 닫으면
    그 티켓은 답을 아예 못 보냅니다 — 기계 주소로라도 나가면 고객은 답을 받고 대화도 그
    자리에 이어집니다. 화면에는 그 주소가 그대로 적힙니다.
    """
    monkeypatch.setattr(
        settings, "HUBSPOT_DEFAULT_EMAIL_CHANNEL_ACCOUNT_ID", "elsewhere"
    )
    monkeypatch.setattr(settings, "HUBSPOT_PREFERRED_EMAIL_CHANNEL_ACCOUNT_ID", "")
    _thread_with_one_email("t1", "inbox-1", "relay-1")
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/channel-accounts/relay-1"
    ).mock(
        return_value=httpx.Response(200, json={
            "id": "relay-1", "channelId": "1002", "inboxId": "inbox-1",
            "active": True, "authorized": True, "archived": False,
            "deliveryIdentifier": {"value": "support@45169260.hubspot-inbox.com"},
        })
    )
    respx.get(
        f"{BASE_URL}/conversations/v3/conversations/channel-accounts/elsewhere"
    ).mock(
        return_value=httpx.Response(200, json={
            "id": "elsewhere", "channelId": "1002", "inboxId": "inbox-9",
            "active": True, "authorized": True, "archived": False,
            "deliveryIdentifier": {"value": "support@perso.ai"},
        })
    )

    context = await client.find_default_reply_context("ticket-1", "buyer@example.com")

    assert context.channel_account_id == "relay-1"


@respx.mock
@pytest.mark.asyncio
async def test_an_undecidable_ticket_says_why_instead_of_going_quiet(
    client: HubSpotClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**고를 것이 없는 것과 못 가져온 것은 다릅니다** (2026-09-03).

    같은 인박스에 스레드가 둘인데 그 고객과 오간 메일이 없으면 어느 쪽에 붙일지 정할 수
    없어 기본값 결정이 실패합니다(운영 3건: 35003648794 · 35313028142 · 37308868745).
    예전에는 그 예외가 `list_reply_senders` 전체를 죽여 라우트가 `{senders: []}` 를 돌려줬고,
    화면은 이유를 안 그려 **고르개가 소리 없이 사라졌습니다.** 그 티켓은 발송도 같은 이유로
    실패하므로, 운영자가 눌러 보기 전에 알아야 합니다.
    """
    monkeypatch.setattr(settings, "HUBSPOT_PREFERRED_EMAIL_CHANNEL_ACCOUNT_ID", "")
    monkeypatch.setattr(settings, "HUBSPOT_DEFAULT_EMAIL_CHANNEL_ACCOUNT_ID", "team-account")
    respx.get(f"{BASE_URL}/conversations/v3/conversations/threads").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "t1", "inboxId": "inbox-1"},
            {"id": "t2", "inboxId": "inbox-1"},
        ]})
    )
    for thread_id in ("t1", "t2"):
        respx.get(
            f"{BASE_URL}/conversations/v3/conversations/threads/{thread_id}/messages"
        ).mock(return_value=httpx.Response(200, json={"results": [
            {"type": "MESSAGE", "createdAt": "2026-02-01T00:00:00Z", "channelId": "1003"},
        ]}))
    respx.get(f"{BASE_URL}/conversations/v3/conversations/channel-accounts").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "team-account", "channelId": "1002", "inboxId": "inbox-1",
             "active": True, "authorized": True, "archived": False,
             "deliveryIdentifier": {"value": "support@perso.ai"}},
        ]})
    )

    found = await client.list_reply_senders("ticket-1", "buyer@example.com")

    # 예외로 죽지 않습니다 — 목록은 비지만 이유가 실려 옵니다.
    assert found["senders"] == []
    assert "2 possible threads" in found["reason"]


@respx.mock
@pytest.mark.asyncio
async def test_a_relay_default_is_still_named_on_screen(client: HubSpotClient) -> None:
    """**고를 수 없는 주소가 기본값일 때도 화면은 그 이름을 적습니다** (2026-09-03).

    기계 주소는 고르개에서 빼지만 기본값일 수는 있습니다(운영 실측: 티켓 103건 중 2건).
    목록에서만 빼고 끝내면 그 티켓에서는 `is_default` 인 줄이 하나도 없어, 화면의
    「자동 — …」이 「이 대화의 주소」라는 두루뭉술한 말로 떨어집니다 — 그러면 **어느 주소로
    나갈지가 화면 어디에도 안 적힙니다.** 목록과 별개로 이름을 돌려줍니다.
    """
    _thread_with_one_email("t1", "inbox-1", "relay-1")
    respx.get(f"{BASE_URL}/conversations/v3/conversations/channel-accounts").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "relay-1", "channelId": "1002", "inboxId": "inbox-1",
             "active": True, "authorized": True, "archived": False,
             "deliveryIdentifier": {"value": "support@45169260.hubspot-inbox.com"}},
            {"id": "team-account", "channelId": "1002", "inboxId": "inbox-1",
             "active": True, "authorized": True, "archived": False,
             "deliveryIdentifier": {"value": "support@perso.ai"}},
        ]})
    )

    found = await client.list_reply_senders("ticket-1", "buyer@example.com")

    assert found["default_address"] == "support@45169260.hubspot-inbox.com"
    assert [s["address"] for s in found["senders"]] == ["support@perso.ai"]
    assert all(not s["is_default"] for s in found["senders"])


@respx.mock
@pytest.mark.asyncio
async def test_hubspot_relay_addresses_are_not_offered(client: HubSpotClient) -> None:
    """**허브스팟 내부 전달 주소는 고를 수 없습니다** (2026-09-03 운영자 지시).

    인박스를 연결하면 허브스팟이 `support@45169260.hubspot-inbox.com` 같은 주소를 자동으로
    발급합니다. 채널 계정 목록에는 뜨지만 고객이 받는 메일의 보낸사람이 저 기계 주소가
    되므로, 사람이 고를 자리에 두면 안 됩니다 — 두면 언젠가 골라집니다.
    """
    _thread_with_one_email("t1", "inbox-1", "team-account")
    respx.get(f"{BASE_URL}/conversations/v3/conversations/channel-accounts").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "team-account", "channelId": "1002", "inboxId": "inbox-1",
             "active": True, "authorized": True, "archived": False,
             "deliveryIdentifier": {"value": "support@perso.ai"}},
            {"id": "relay-1", "channelId": "1002", "inboxId": "inbox-1",
             "active": True, "authorized": True, "archived": False,
             "deliveryIdentifier": {"value": "support@45169260.hubspot-inbox.com"}},
            {"id": "relay-2", "channelId": "1002", "inboxId": "inbox-1",
             "active": True, "authorized": True, "archived": False,
             "deliveryIdentifier": {"value": "support@perso.co.kr.hs-inbox.com"}},
        ]})
    )

    found = await client.list_reply_senders("ticket-1", "buyer@example.com")
    senders = found["senders"]

    assert [s["address"] for s in senders] == ["support@perso.ai"]


@respx.mock
@pytest.mark.asyncio
async def test_the_picker_follows_the_paging_cursor(
    client: HubSpotClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """채널 계정 목록은 **커서를 끝까지 따라갑니다.**

    예전에는 `limit=200` 한 번으로 끝냈습니다. 이 포털은 66개라 아직 안 물렸지만, 넘는
    순간 뒤쪽 계정이 고르개에서 **조용히** 사라집니다 — 에러도 경고도 없이 목록만 짧아지고,
    운영자는 그 주소를 못 고르는 이유를 알 방법이 없습니다. 페이지 크기는 우리가 정하는
    값이 아니라, 끝을 아는 길은 커서뿐입니다.
    """
    monkeypatch.setattr(
        settings, "HUBSPOT_REPLY_SENDER_ACCOUNT_IDS", "team-account, second-page"
    )
    _thread_with_one_email("t1", "inbox-1", "team-account")

    pages = [
        httpx.Response(200, json={
            "results": [
                {"id": "team-account", "channelId": "1002", "inboxId": "inbox-1",
                 "active": True, "authorized": True, "archived": False,
                 "deliveryIdentifier": {"value": "perso.ai@estsoft.com"}},
            ],
            "paging": {"next": {"after": "CURSOR-1"}},
        }),
        httpx.Response(200, json={
            "results": [
                # 둘째 페이지에만 있는 주소. 커서를 안 따라가면 이 줄이 사라집니다.
                {"id": "second-page", "channelId": "1002", "inboxId": "inbox-1",
                 "active": True, "authorized": True, "archived": False,
                 "deliveryIdentifier": {"value": "later@estsoft.com"}},
            ],
        }),
    ]
    route = respx.get(
        f"{BASE_URL}/conversations/v3/conversations/channel-accounts"
    ).mock(side_effect=pages)

    found = await client.list_reply_senders("ticket-1", "buyer@example.com")

    assert route.call_count == 2, "커서가 있으면 다음 페이지를 받아야 합니다"
    assert "after=CURSOR-1" in str(route.calls[1].request.url)
    assert "later@estsoft.com" in {s["address"] for s in found["senders"]}
