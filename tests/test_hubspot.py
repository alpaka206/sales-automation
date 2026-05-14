"""Tests for HubSpot client using respx mocks."""

from __future__ import annotations

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
    with pytest.raises(HubSpotNotConfigured):
        HubSpotClient(token="")


def test_no_token_from_settings_raises() -> None:
    from unittest.mock import patch

    with patch("src.integrations.hubspot.settings") as mock_settings:
        mock_settings.HUBSPOT_PRIVATE_APP_TOKEN = ""
        with pytest.raises(HubSpotNotConfigured):
            HubSpotClient()
