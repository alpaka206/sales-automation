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


@respx.mock
@pytest.mark.asyncio
async def test_create_email_engagement(client: HubSpotClient) -> None:
    respx.post(f"{BASE_URL}/crm/v3/objects/emails").mock(
        return_value=httpx.Response(200, json={"id": "eng-1"})
    )
    respx.put(url__regex=r".*/associations/.*").mock(
        return_value=httpx.Response(200, json={})
    )

    eng_id = await client.create_email_engagement(
        contact_id="123",
        subject="Hi",
        body="Body text",
        sent_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    await client.close()
    assert eng_id == "eng-1"


@respx.mock
@pytest.mark.asyncio
async def test_list_contact_engagements(client: HubSpotClient) -> None:
    respx.get(
        f"{BASE_URL}/crm/v3/objects/contacts/123/associations/emails"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"id": "e1"},
                    {"id": "e2"},
                ],
            },
        )
    )
    engs = await client.list_contact_engagements("123")
    await client.close()
    assert len(engs) == 2
    assert engs[0].id == "e1"


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
