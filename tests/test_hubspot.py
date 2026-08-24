"""Tests for HubSpot client using respx mocks."""

from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest
import respx

from src.integrations.hubspot import (
    BASE_URL,
    ContactDTO,
    HubSpotAPIError,
    HubSpotClient,
    HubSpotNotConfigured,
)


@pytest.fixture()
def client() -> HubSpotClient:
    return HubSpotClient(token="test-token")


@respx.mock
@pytest.mark.asyncio
async def test_get_contact_by_id(client: HubSpotClient) -> None:
    respx.get(f"{BASE_URL}/crm/v3/objects/contacts/123").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "123",
                "properties": {
                    "email": "test@example.com",
                    "firstname": "Test",
                    "lastname": "User",
                    "company": "Acme",
                    "lifecyclestage": "lead",
                },
            },
        )
    )
    contact = await client.get_contact("123")
    await client.close()

    assert isinstance(contact, ContactDTO)
    assert contact.id == "123"
    assert contact.email == "test@example.com"
    assert contact.company == "Acme"


@respx.mock
@pytest.mark.asyncio
async def test_get_contact_not_found(client: HubSpotClient) -> None:
    respx.get(f"{BASE_URL}/crm/v3/objects/contacts/999").mock(
        return_value=httpx.Response(404, json={"status": "error", "message": "not found"})
    )
    with pytest.raises(HubSpotAPIError, match="not found"):
        await client.get_contact("999")
    await client.close()


def test_no_token_raises() -> None:
    from unittest.mock import patch

    # Mock settings so the test is independent of whatever the dev's .env contains.
    with patch("src.integrations.hubspot.settings") as mock_settings:
        mock_settings.HUBSPOT_PRIVATE_APP_TOKEN = ""
        with pytest.raises(HubSpotNotConfigured):
            HubSpotClient(token="")


def test_no_token_from_settings_raises() -> None:
    from unittest.mock import patch

    with patch("src.integrations.hubspot.settings") as mock_settings:
        mock_settings.HUBSPOT_PRIVATE_APP_TOKEN = ""
        with pytest.raises(HubSpotNotConfigured):
            HubSpotClient()


def test_move_ticket_stage_reports_result() -> None:
    from unittest.mock import patch

    from src.integrations.hubspot import move_ticket_stage_after_send

    with patch("src.integrations.hubspot.settings") as mock_settings, patch(
        "src.integrations.hubspot.HubSpotClient"
    ) as mock_client:
        mock_settings.HUBSPOT_TICKET_STAGE_AFTER_SEND = "meeting-link-sent"
        assert move_ticket_stage_after_send("ticket-1") is True
        mock_client.return_value.update_ticket_stage_sync.side_effect = RuntimeError("down")
        assert move_ticket_stage_after_send("ticket-2") is False


@respx.mock
def test_search_tickets_follows_paging(client: HubSpotClient) -> None:
    route = respx.post(f"{BASE_URL}/crm/v3/objects/tickets/search")
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "results": [{"id": "1", "properties": {}}],
                "paging": {"next": {"after": "cursor-2"}},
            },
        ),
        httpx.Response(200, json={"results": [{"id": "2", "properties": {}}]}),
    ]

    tickets = client.search_tickets_sync(datetime(2026, 7, 18), limit=1000)

    assert [ticket.id for ticket in tickets] == ["1", "2"]
    assert route.call_count == 2
    first_body = json.loads(route.calls[0].request.content)
    assert first_body["filterGroups"][0]["filters"][0]["propertyName"] == "hs_lastmodifieddate"
    assert first_body["sorts"][0]["propertyName"] == "hs_lastmodifieddate"
    assert json.loads(route.calls[1].request.content)["after"] == "cursor-2"


@respx.mock
def test_bulk_ticket_walk_retries_a_429_instead_of_aborting(client: HubSpotClient, monkeypatch) -> None:
    """The backfill walks ~30 pages back to back and tripped HubSpot's 100/10s cap.

    A 429 on page 2 used to raise straight out of raise_for_status(), and since the
    backfill is not resumable the whole run restarted from page 1 every time.
    """
    monkeypatch.setattr("src.integrations.hubspot.time.sleep", lambda *_: None)
    route = respx.get(f"{BASE_URL}/crm/v3/objects/tickets")
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "1",
                        "properties": {"hs_pipeline": "798618015"},
                        "associations": {"contacts": {"results": [{"id": "c1"}]}},
                    }
                ],
                "paging": {"next": {"after": "cursor-2"}},
            },
        ),
        httpx.Response(429, headers={"retry-after": "0"}, json={"message": "rate limited"}),
        httpx.Response(
            200,
            json={
                "results": [
                    {"id": "2", "properties": {"hs_pipeline": "other"}},
                    {"id": "3", "properties": {"hs_pipeline": "798618015"}},
                ]
            },
        ),
    ]

    pairs = client.list_tickets_with_contacts_sync(pipeline="798618015")

    assert route.call_count == 3, "the 429 must be retried, not raised"
    # Only the requested pipeline survives; page 2's other-pipeline ticket is dropped.
    assert [t.id for t, _ in pairs] == ["1", "3"]
    assert pairs[0][1] == ["c1"]


@respx.mock
def test_contacts_batch_read_retries_a_429(client: HubSpotClient, monkeypatch) -> None:
    monkeypatch.setattr("src.integrations.hubspot.time.sleep", lambda *_: None)
    route = respx.post(f"{BASE_URL}/crm/v3/objects/contacts/batch/read")
    route.side_effect = [
        httpx.Response(429, headers={"retry-after": "0"}, json={"message": "rate limited"}),
        httpx.Response(
            200,
            json={"results": [{"id": "c1", "properties": {"email": "a@b.com"}}]},
        ),
    ]

    got = client.get_contacts_batch_sync(["c1"])

    assert route.call_count == 2
    assert got["c1"].email == "a@b.com"


@respx.mock
def test_a_partly_missing_batch_answered_with_207_keeps_the_survivors(
    client: HubSpotClient,
) -> None:
    """지워진 티켓은 결과에서 빠집니다 — 삭제된 객체는 보관함으로 가고, 평범한 읽기는
    보관된 것을 안 돌려줍니다."""
    respx.post(f"{BASE_URL}/crm/v3/objects/tickets/batch/read").mock(
        return_value=httpx.Response(207, json={"results": [{"id": "1"}]})
    )

    assert client.existing_ticket_ids_sync(["1", "2"]) == {"1"}


@respx.mock
def test_a_batch_refused_with_404_is_asked_again_one_at_a_time(client: HubSpotClient) -> None:
    """HubSpot 이 부분 실패를 늘 207 로 주지는 않습니다 — 통째로 404 로 거절하기도 하고,
    그게 바로 우리가 물어본 답입니다. 「배치 실패」로 읽으면 지울 게 있을 때마다 삭제
    검사를 건너뛰어서, 지워진 티켓이 최신화를 몇 번 눌러도 화면에 남았습니다."""
    respx.post(f"{BASE_URL}/crm/v3/objects/tickets/batch/read").mock(
        return_value=httpx.Response(404, json={"message": "Could not get some TICKET objects"})
    )
    respx.get(f"{BASE_URL}/crm/v3/objects/tickets/1").mock(return_value=httpx.Response(200))
    respx.get(f"{BASE_URL}/crm/v3/objects/tickets/2").mock(return_value=httpx.Response(404))

    assert client.existing_ticket_ids_sync(["1", "2"]) == {"1"}


@respx.mock
def test_a_token_failure_is_never_read_as_all_gone(client: HubSpotClient, monkeypatch) -> None:
    """단건으로 물어봐도 401 은 「없다」가 아닙니다. 여기서 빈 집합을 돌려주면 확인 한 번에
    보드가 통째로 지워집니다."""
    monkeypatch.setattr("src.integrations.hubspot.time.sleep", lambda *_: None)
    respx.post(f"{BASE_URL}/crm/v3/objects/tickets/batch/read").mock(
        return_value=httpx.Response(404, json={"message": "nope"})
    )
    respx.get(f"{BASE_URL}/crm/v3/objects/tickets/1").mock(return_value=httpx.Response(401))

    with pytest.raises(HubSpotAPIError):
        client.existing_ticket_ids_sync(["1"])
