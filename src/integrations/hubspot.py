"""HubSpot CRM v3 async client."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx
from pydantic import BaseModel

from ..common.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"


class HubSpotNotConfigured(RuntimeError):
    pass


class HubSpotAPIError(RuntimeError):
    pass


class ContactDTO(BaseModel):
    id: str
    email: str | None = None
    firstname: str | None = None
    lastname: str | None = None
    company: str | None = None
    phone: str | None = None
    country: str | None = None
    lifecyclestage: str | None = None


class EngagementDTO(BaseModel):
    id: str
    type: str
    subject: str | None = None
    body: str | None = None
    timestamp: datetime | None = None


class DealDTO(BaseModel):
    id: str
    name: str | None = None
    stage: str | None = None
    amount: str | None = None


def _require_token() -> str:
    token = settings.HUBSPOT_PRIVATE_APP_TOKEN
    if not token:
        raise HubSpotNotConfigured(
            "HUBSPOT_PRIVATE_APP_TOKEN is not set. HubSpot calls are unavailable."
        )
    return token


class HubSpotClient:
    """Thin async wrapper around HubSpot CRM v3."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token or _require_token()
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get_contact(self, id_or_email: str) -> ContactDTO:
        """Fetch a contact by ID or email."""
        http = await self._http()
        if "@" in id_or_email:
            url = f"/crm/v3/objects/contacts/{id_or_email}"
            params = {"idProperty": "email"}
        else:
            url = f"/crm/v3/objects/contacts/{id_or_email}"
            params = {}

        r = await http.get(url, params=params)
        if r.status_code == 404:
            raise HubSpotAPIError(f"Contact not found: {id_or_email}")
        r.raise_for_status()
        data = r.json()
        props = data.get("properties", {})
        return ContactDTO(
            id=str(data["id"]),
            email=props.get("email"),
            firstname=props.get("firstname"),
            lastname=props.get("lastname"),
            company=props.get("company"),
            lifecyclestage=props.get("lifecyclestage"),
        )

    async def update_contact(self, contact_id: str, properties: dict) -> None:
        """Update a contact's properties."""
        http = await self._http()
        r = await http.patch(
            f"/crm/v3/objects/contacts/{contact_id}",
            json={"properties": properties},
        )
        r.raise_for_status()

    async def list_contact_engagements(
        self,
        contact_id: str,
        since: datetime | None = None,
        limit: int = 10,
    ) -> list[EngagementDTO]:
        """List recent engagements for a contact."""
        http = await self._http()
        r = await http.get(
            f"/crm/v3/objects/contacts/{contact_id}/associations/emails",
            params={"limit": limit},
        )
        r.raise_for_status()
        results = r.json().get("results", [])

        engagements: list[EngagementDTO] = []
        for item in results:
            engagements.append(
                EngagementDTO(
                    id=str(item.get("id", "")),
                    type="email",
                )
            )
        return engagements

    async def create_email_engagement(
        self,
        contact_id: str,
        subject: str,
        body: str,
        sent_at: datetime | None = None,
    ) -> str:
        """Log an email engagement on the contact's timeline. Returns engagement ID."""
        http = await self._http()
        ts = int((sent_at or datetime.utcnow()).timestamp() * 1000)
        payload = {
            "properties": {
                "hs_timestamp": str(ts),
                "hubspot_owner_id": settings.HUBSPOT_OWNER_ID or None,
                "hs_email_direction": "EMAIL",
                "hs_email_subject": subject,
                "hs_email_text": body,
                "hs_email_status": "SENT",
            },
        }
        r = await http.post("/crm/v3/objects/emails", json=payload)
        r.raise_for_status()
        email_id = r.json()["id"]

        await http.put(
            f"/crm/v3/objects/emails/{email_id}/associations/contacts/{contact_id}/198",
        )
        logger.info("Logged email engagement %s for contact %s", email_id, contact_id)
        return email_id

    async def send_email(
        self,
        contact_id: str,
        subject: str,
        body: str,
        from_email: str,
    ) -> str:
        """Send an email via HubSpot single-send and log engagement. Returns engagement ID."""
        return await self.create_email_engagement(contact_id, subject, body)

    # ------ Sync helpers (for use in synchronous agent code) ------

    def search_contacts_sync(
        self,
        created_after: datetime,
        lifecycle_stage: str = "lead",
        limit: int = 100,
    ) -> list[ContactDTO]:
        """Search contacts created after a given timestamp with a specific lifecycle stage."""
        headers = {"Authorization": f"Bearer {self.token}"}
        ts_ms = str(int(created_after.timestamp() * 1000))
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "createdate", "operator": "GT", "value": ts_ms},
                        {"propertyName": "lifecyclestage", "operator": "EQ", "value": lifecycle_stage},
                    ]
                }
            ],
            "sorts": [{"propertyName": "createdate", "direction": "ASCENDING"}],
            "properties": ["email", "firstname", "lastname", "company", "phone", "country", "lifecyclestage"],
            "limit": limit,
        }
        with httpx.Client(headers=headers, timeout=30.0) as client:
            r = client.post(f"{BASE_URL}/crm/v3/objects/contacts/search", json=body)
        r.raise_for_status()
        results = r.json().get("results", [])
        contacts: list[ContactDTO] = []
        for item in results:
            props = item.get("properties", {})
            contacts.append(ContactDTO(
                id=str(item["id"]),
                email=props.get("email"),
                firstname=props.get("firstname"),
                lastname=props.get("lastname"),
                company=props.get("company"),
                phone=props.get("phone"),
                country=props.get("country"),
                lifecyclestage=props.get("lifecyclestage"),
            ))
        return contacts

    def get_contact_sync(self, id_or_email: str) -> ContactDTO:
        """Synchronous version of get_contact."""
        if "@" in id_or_email:
            url = f"{BASE_URL}/crm/v3/objects/contacts/{id_or_email}"
            params = {"idProperty": "email"}
        else:
            url = f"{BASE_URL}/crm/v3/objects/contacts/{id_or_email}"
            params = {}

        with httpx.Client(headers={"Authorization": f"Bearer {self.token}"}, timeout=30.0) as client:
            r = client.get(url, params=params)
        if r.status_code == 404:
            raise HubSpotAPIError(f"Contact not found: {id_or_email}")
        r.raise_for_status()
        data = r.json()
        props = data.get("properties", {})
        return ContactDTO(
            id=str(data["id"]),
            email=props.get("email"),
            firstname=props.get("firstname"),
            lastname=props.get("lastname"),
            company=props.get("company"),
            phone=props.get("phone"),
            country=props.get("country"),
            lifecyclestage=props.get("lifecyclestage"),
        )

    def get_recent_emails_sync(self, contact_id: str, limit: int = 5) -> list[EngagementDTO]:
        """Fetch recent email engagements with content for a contact (sync)."""
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers, timeout=30.0) as client:
            r = client.get(
                f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}/associations/emails",
                params={"limit": limit},
            )
            r.raise_for_status()
            assoc_results = r.json().get("results", [])

            engagements: list[EngagementDTO] = []
            for item in assoc_results:
                email_id = str(item.get("id", ""))
                if not email_id:
                    continue
                er = client.get(
                    f"{BASE_URL}/crm/v3/objects/emails/{email_id}",
                    params={"properties": "hs_email_subject,hs_email_text,hs_timestamp"},
                )
                if er.status_code != 200:
                    continue
                ep = er.json().get("properties", {})
                engagements.append(EngagementDTO(
                    id=email_id,
                    type="email",
                    subject=ep.get("hs_email_subject"),
                    body=ep.get("hs_email_text"),
                    timestamp=datetime.fromisoformat(ep["hs_timestamp"]) if ep.get("hs_timestamp") else None,
                ))
        return engagements

    def get_associated_deals_sync(self, contact_id: str) -> list[DealDTO]:
        """Fetch deals associated with a contact (sync)."""
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers, timeout=30.0) as client:
            r = client.get(
                f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}/associations/deals",
                params={"limit": 10},
            )
            r.raise_for_status()
            assoc_results = r.json().get("results", [])

            deals: list[DealDTO] = []
            for item in assoc_results:
                deal_id = str(item.get("id", ""))
                if not deal_id:
                    continue
                dr = client.get(
                    f"{BASE_URL}/crm/v3/objects/deals/{deal_id}",
                    params={"properties": "dealname,dealstage,amount"},
                )
                if dr.status_code != 200:
                    continue
                dp = dr.json().get("properties", {})
                deals.append(DealDTO(
                    id=deal_id,
                    name=dp.get("dealname"),
                    stage=dp.get("dealstage"),
                    amount=dp.get("amount"),
                ))
        return deals
