"""Tests for HubSpot client sync methods."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest
import respx

from src.integrations.hubspot import (
    BASE_URL,
    ContactDTO,
    DealDTO,
    EngagementDTO,
    HubSpotAPIError,
    HubSpotClient,
)


@pytest.fixture()
def client() -> HubSpotClient:
    return HubSpotClient(token="test-token")


# ---------- get_contact by email (covers lines 85-86) ----------


@respx.mock
@pytest.mark.asyncio
async def test_get_contact_by_email(client: HubSpotClient) -> None:
    respx.get(f"{BASE_URL}/crm/v3/objects/contacts/user@example.com").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "42",
                "properties": {
                    "email": "user@example.com",
                    "firstname": "Jane",
                    "lastname": "Doe",
                    "company": "Acme",
                    "lifecyclestage": "lead",
                },
            },
        )
    )
    contact = await client.get_contact("user@example.com")
    await client.close()
    assert contact.id == "42"
    assert contact.email == "user@example.com"


# ---------- update_inbound_status success (covers line 119) ----------


@respx.mock
@pytest.mark.asyncio
async def test_update_inbound_status_success(client: HubSpotClient) -> None:
    respx.patch(f"{BASE_URL}/crm/v3/objects/contacts/100").mock(
        return_value=httpx.Response(200, json={})
    )
    await client.update_inbound_status("100", "analyzed")
    await client.close()


# ---------- update_inbound_status 400 (covers lines 121-126) ----------


@respx.mock
@pytest.mark.asyncio
async def test_update_inbound_status_400_warns(client: HubSpotClient) -> None:
    respx.patch(f"{BASE_URL}/crm/v3/objects/contacts/101").mock(
        return_value=httpx.Response(400, json={"message": "property not found"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.update_inbound_status("101", "analyzed")
    await client.close()


# ---------- send_email delegates (covers line 191) ----------


@respx.mock
@pytest.mark.asyncio
async def test_send_email(client: HubSpotClient) -> None:
    respx.post(f"{BASE_URL}/crm/v3/objects/emails").mock(
        return_value=httpx.Response(200, json={"id": "eng-99"})
    )
    respx.put(url__regex=r".*/associations/.*").mock(
        return_value=httpx.Response(200, json={})
    )
    eng_id = await client.send_email("123", "Sub", "Body", "from@x.com")
    await client.close()
    assert eng_id == "eng-99"


# ---------- search_contacts_sync (covers lines 202-234) ----------


@respx.mock
def test_search_contacts_sync(client: HubSpotClient) -> None:
    respx.post(f"{BASE_URL}/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "10",
                        "properties": {
                            "email": "lead@co.kr",
                            "firstname": "Kim",
                            "lastname": "Lee",
                            "company": "Co",
                            "phone": "010-0000",
                            "country": "KR",
                            "lifecyclestage": "lead",
                        },
                    }
                ]
            },
        )
    )
    contacts = client.search_contacts_sync(
        created_after=datetime(2025, 1, 1), lifecycle_stage="lead"
    )
    assert len(contacts) == 1
    assert contacts[0].email == "lead@co.kr"
    assert contacts[0].country == "KR"


@respx.mock
def test_search_contacts_sync_empty(client: HubSpotClient) -> None:
    respx.post(f"{BASE_URL}/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    contacts = client.search_contacts_sync(created_after=datetime(2025, 1, 1))
    assert contacts == []


# ---------- update_inbound_status_sync (covers lines 236-250) ----------


@respx.mock
def test_update_inbound_status_sync_success(client: HubSpotClient) -> None:
    respx.patch(f"{BASE_URL}/crm/v3/objects/contacts/200").mock(
        return_value=httpx.Response(200, json={})
    )
    client.update_inbound_status_sync("200", "analyzed")


@respx.mock
def test_update_inbound_status_sync_400(client: HubSpotClient) -> None:
    respx.patch(f"{BASE_URL}/crm/v3/objects/contacts/201").mock(
        return_value=httpx.Response(400, json={"message": "bad"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.update_inbound_status_sync("201", "analyzed")


# ---------- get_contact_sync (covers lines 252-277) ----------


@respx.mock
def test_get_contact_sync_by_id(client: HubSpotClient) -> None:
    respx.get(f"{BASE_URL}/crm/v3/objects/contacts/300").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "300",
                "properties": {
                    "email": "c@test.com",
                    "firstname": "A",
                    "lastname": "B",
                    "company": "C",
                    "phone": "000",
                    "country": "KR",
                    "lifecyclestage": "customer",
                },
            },
        )
    )
    c = client.get_contact_sync("300")
    assert c.id == "300"
    assert c.email == "c@test.com"


@respx.mock
def test_get_contact_sync_by_email(client: HubSpotClient) -> None:
    respx.get(f"{BASE_URL}/crm/v3/objects/contacts/e@test.com").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "301",
                "properties": {"email": "e@test.com"},
            },
        )
    )
    c = client.get_contact_sync("e@test.com")
    assert c.id == "301"


@respx.mock
def test_get_contact_sync_404(client: HubSpotClient) -> None:
    respx.get(f"{BASE_URL}/crm/v3/objects/contacts/999").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )
    with pytest.raises(HubSpotAPIError, match="not found"):
        client.get_contact_sync("999")


# ---------- get_recent_emails_sync (covers lines 279-309) ----------


@respx.mock
def test_get_recent_emails_sync(client: HubSpotClient) -> None:
    respx.get(
        f"{BASE_URL}/crm/v3/objects/contacts/400/associations/emails"
    ).mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": "e10"}, {"id": "e11"}]}
        )
    )
    respx.get(f"{BASE_URL}/crm/v3/objects/emails/e10").mock(
        return_value=httpx.Response(
            200,
            json={
                "properties": {
                    "hs_email_subject": "Hello",
                    "hs_email_text": "Body",
                    "hs_timestamp": "2025-06-01T00:00:00",
                }
            },
        )
    )
    respx.get(f"{BASE_URL}/crm/v3/objects/emails/e11").mock(
        return_value=httpx.Response(404, json={})
    )
    engs = client.get_recent_emails_sync("400")
    assert len(engs) == 1
    assert engs[0].subject == "Hello"
    assert engs[0].body == "Body"


@respx.mock
def test_get_recent_emails_sync_empty(client: HubSpotClient) -> None:
    respx.get(
        f"{BASE_URL}/crm/v3/objects/contacts/401/associations/emails"
    ).mock(return_value=httpx.Response(200, json={"results": []}))
    engs = client.get_recent_emails_sync("401")
    assert engs == []


# ---------- get_latest_form_submission (covers lines 311-325) ----------


@respx.mock
def test_get_latest_form_submission_found(client: HubSpotClient) -> None:
    respx.get(f"{BASE_URL}/crm/v3/objects/contacts/500").mock(
        return_value=httpx.Response(
            200,
            json={
                "properties": {
                    "hs_latest_source": "FORM_SUBMISSION",
                    "hs_latest_source_data_2": "I want a demo",
                }
            },
        )
    )
    result = client.get_latest_form_submission("500")
    assert result == "I want a demo"


@respx.mock
def test_get_latest_form_submission_not_form(client: HubSpotClient) -> None:
    respx.get(f"{BASE_URL}/crm/v3/objects/contacts/501").mock(
        return_value=httpx.Response(
            200,
            json={
                "properties": {
                    "hs_latest_source": "ORGANIC_SEARCH",
                    "hs_latest_source_data_2": "something",
                }
            },
        )
    )
    assert client.get_latest_form_submission("501") is None


@respx.mock
def test_get_latest_form_submission_404(client: HubSpotClient) -> None:
    respx.get(f"{BASE_URL}/crm/v3/objects/contacts/502").mock(
        return_value=httpx.Response(404, json={})
    )
    assert client.get_latest_form_submission("502") is None


# ---------- get_latest_inbound_email (covers lines 327-352) ----------


@respx.mock
def test_get_latest_inbound_email_found(client: HubSpotClient) -> None:
    respx.get(
        f"{BASE_URL}/crm/v3/objects/contacts/600/associations/emails"
    ).mock(
        return_value=httpx.Response(200, json={"results": [{"id": "em1"}]})
    )
    respx.get(f"{BASE_URL}/crm/v3/objects/emails/em1").mock(
        return_value=httpx.Response(
            200,
            json={
                "properties": {
                    "hs_email_direction": "INCOMING_EMAIL",
                    "hs_email_text": "Please help",
                    "hs_email_subject": "Re: Question",
                }
            },
        )
    )
    assert client.get_latest_inbound_email("600") == "Please help"


@respx.mock
def test_get_latest_inbound_email_outgoing_only(client: HubSpotClient) -> None:
    respx.get(
        f"{BASE_URL}/crm/v3/objects/contacts/601/associations/emails"
    ).mock(
        return_value=httpx.Response(200, json={"results": [{"id": "em2"}]})
    )
    respx.get(f"{BASE_URL}/crm/v3/objects/emails/em2").mock(
        return_value=httpx.Response(
            200,
            json={
                "properties": {
                    "hs_email_direction": "EMAIL",
                    "hs_email_text": "Outgoing",
                }
            },
        )
    )
    assert client.get_latest_inbound_email("601") is None


@respx.mock
def test_get_latest_inbound_email_assoc_fail(client: HubSpotClient) -> None:
    respx.get(
        f"{BASE_URL}/crm/v3/objects/contacts/602/associations/emails"
    ).mock(return_value=httpx.Response(500, json={}))
    assert client.get_latest_inbound_email("602") is None


# ---------- get_latest_note (covers lines 354-376) ----------


@respx.mock
def test_get_latest_note_found(client: HubSpotClient) -> None:
    respx.get(
        f"{BASE_URL}/crm/v3/objects/contacts/700/associations/notes"
    ).mock(
        return_value=httpx.Response(200, json={"results": [{"id": "n1"}]})
    )
    respx.get(f"{BASE_URL}/crm/v3/objects/notes/n1").mock(
        return_value=httpx.Response(
            200,
            json={"properties": {"hs_note_body": "Important note"}},
        )
    )
    assert client.get_latest_note("700") == "Important note"


@respx.mock
def test_get_latest_note_no_results(client: HubSpotClient) -> None:
    respx.get(
        f"{BASE_URL}/crm/v3/objects/contacts/701/associations/notes"
    ).mock(return_value=httpx.Response(200, json={"results": []}))
    assert client.get_latest_note("701") is None


@respx.mock
def test_get_latest_note_assoc_fail(client: HubSpotClient) -> None:
    respx.get(
        f"{BASE_URL}/crm/v3/objects/contacts/702/associations/notes"
    ).mock(return_value=httpx.Response(500, json={}))
    assert client.get_latest_note("702") is None


@respx.mock
def test_get_latest_note_detail_fail(client: HubSpotClient) -> None:
    respx.get(
        f"{BASE_URL}/crm/v3/objects/contacts/703/associations/notes"
    ).mock(return_value=httpx.Response(200, json={"results": [{"id": "n2"}]}))
    respx.get(f"{BASE_URL}/crm/v3/objects/notes/n2").mock(
        return_value=httpx.Response(500, json={})
    )
    assert client.get_latest_note("703") is None


# ---------- get_associated_deals_sync (covers lines 378-407) ----------


@respx.mock
def test_get_associated_deals_sync(client: HubSpotClient) -> None:
    respx.get(
        f"{BASE_URL}/crm/v3/objects/contacts/800/associations/deals"
    ).mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": "d1"}, {"id": "d2"}]}
        )
    )
    respx.get(f"{BASE_URL}/crm/v3/objects/deals/d1").mock(
        return_value=httpx.Response(
            200,
            json={
                "properties": {
                    "dealname": "Big Deal",
                    "dealstage": "closedwon",
                    "amount": "50000",
                }
            },
        )
    )
    respx.get(f"{BASE_URL}/crm/v3/objects/deals/d2").mock(
        return_value=httpx.Response(404, json={})
    )
    deals = client.get_associated_deals_sync("800")
    assert len(deals) == 1
    assert deals[0].name == "Big Deal"
    assert deals[0].amount == "50000"


@respx.mock
def test_get_associated_deals_sync_empty(client: HubSpotClient) -> None:
    respx.get(
        f"{BASE_URL}/crm/v3/objects/contacts/801/associations/deals"
    ).mock(return_value=httpx.Response(200, json={"results": []}))
    deals = client.get_associated_deals_sync("801")
    assert deals == []
