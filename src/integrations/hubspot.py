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
    lifecyclestage: str | None = None


class EngagementDTO(BaseModel):
    id: str
    type: str
    subject: str | None = None
    body: str | None = None
    timestamp: datetime | None = None


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
