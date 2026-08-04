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
async def test_email_engagement_is_attached_to_the_ticket(client: HubSpotClient) -> None:
    """The reply has to show up on the 문의 it answers, not only on the contact.

    HubSpot cannot send this mail (no API for it without the transactional add-on), so
    this record is the whole history — and an operator reads history ticket by ticket.
    198 = email→contact, 224 = email→ticket, both HUBSPOT_DEFINED.
    """
    respx.post(f"{BASE_URL}/crm/v3/objects/emails").mock(
        return_value=httpx.Response(200, json={"id": "eng-9"})
    )
    route = respx.put(url__regex=r".*/associations/.*").mock(
        return_value=httpx.Response(200, json={})
    )

    await client.create_email_engagement(
        contact_id="123", subject="Hi", body="Body", ticket_id="T-77"
    )
    await client.close()

    paths = [str(call.request.url.path) for call in route.calls]
    assert "/crm/v3/objects/emails/eng-9/associations/contacts/123/198" in paths
    assert "/crm/v3/objects/emails/eng-9/associations/tickets/T-77/224" in paths


@respx.mock
@pytest.mark.asyncio
async def test_engagement_survives_a_failed_ticket_association(client: HubSpotClient) -> None:
    """The mail already went out and the engagement already exists — keep them.

    Raising here would send the caller down its "timeline log failed" path for a record
    that was in fact written, and lose the id it needs to stamp on the row.
    """
    respx.post(f"{BASE_URL}/crm/v3/objects/emails").mock(
        return_value=httpx.Response(200, json={"id": "eng-9"})
    )
    respx.put(url__regex=r".*/associations/contacts/.*").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.put(url__regex=r".*/associations/tickets/.*").mock(
        return_value=httpx.Response(404, json={"message": "ticket not found"})
    )

    assert (
        await client.create_email_engagement(
            contact_id="123", subject="Hi", body="Body", ticket_id="gone"
        )
        == "eng-9"
    )
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
