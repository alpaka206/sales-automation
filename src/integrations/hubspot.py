"""HubSpot CRM v3 async client."""

from __future__ import annotations

import asyncio
import html as _html
import logging
import random
import re
from datetime import datetime

import httpx

from ..common.config import settings
from .hubspot_models import ContactDTO, DealDTO, EngagementDTO, TicketDTO

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"

# HubSpot stores rich-text fields (notes, emails sometimes) as HTML. We want plain
# text so the LLM prompts and the approval UI stay clean. Keep paragraph breaks but
# drop every tag and decode entities. Good enough for the simple markup HubSpot emits;
# if HubSpot ever sends pathological markup we can swap in BeautifulSoup.
_BR_RE = re.compile(r"<\s*br\s*/?\s*>", flags=re.IGNORECASE)
_BLOCK_END_RE = re.compile(r"</\s*(p|div|li|h[1-6])\s*>", flags=re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def _html_to_text(s: str | None) -> str | None:
    if not s:
        return s
    s = _BR_RE.sub("\n", s)
    s = _BLOCK_END_RE.sub("\n", s)
    s = _TAG_RE.sub("", s)
    s = _html.unescape(s)
    s = _MULTI_SPACE_RE.sub(" ", s)
    s = _MULTI_NEWLINE_RE.sub("\n\n", s)
    return s.strip() or None

# HubSpot REST returns 429 when over the per-second cap (default 100/10s). 5xx are
# also transient. We retry both with full-jitter exponential backoff.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 4


async def _request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs,
) -> httpx.Response:
    """Send a request, retrying on 429/5xx with exponential backoff.

    Honors HubSpot's `Retry-After` header when present. Other status codes pass
    through (the caller decides whether to raise_for_status)."""
    delay = 1.0
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as exc:
            if attempt == _MAX_RETRIES:
                raise
            logger.warning("HubSpot %s %s transport error (attempt %d): %s", method, url, attempt + 1, exc)
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 2, 30)
            continue

        if response.status_code not in _RETRY_STATUS:
            return response
        if attempt == _MAX_RETRIES:
            return response

        # Prefer Retry-After when the server gives us a hint.
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                wait = float(retry_after)
            except ValueError:
                wait = delay
        else:
            wait = delay
        wait += random.uniform(0, 0.5)
        logger.warning(
            "HubSpot %s %s returned %d (attempt %d), retrying in %.1fs",
            method, url, response.status_code, attempt + 1, wait,
        )
        await asyncio.sleep(wait)
        delay = min(delay * 2, 30)

    return response  # type: ignore[return-value]


class HubSpotNotConfigured(RuntimeError):
    pass


class HubSpotAPIError(RuntimeError):
    pass


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

    async def _retry(self, method: str, url: str, **kw) -> httpx.Response:
        """Internal helper — `await http.request(...)` with retry/backoff."""
        http = await self._http()
        return await _request_with_retries(http, method, url, **kw)

    async def get_contact(self, id_or_email: str) -> ContactDTO:
        """Fetch a contact by ID or email."""
        if "@" in id_or_email:
            url = f"/crm/v3/objects/contacts/{id_or_email}"
            params = {"idProperty": "email"}
        else:
            url = f"/crm/v3/objects/contacts/{id_or_email}"
            params = {}

        r = await self._retry("GET", url, params=params)
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
        r = await self._retry(
            "PATCH",
            f"/crm/v3/objects/contacts/{contact_id}",
            json={"properties": properties},
        )
        r.raise_for_status()

    async def update_inbound_status(self, contact_id: str, status: str) -> None:
        """Update the inbound_status custom property on a contact."""
        try:
            await self.update_contact(contact_id, {"inbound_status": status})
            logger.info("Updated inbound_status=%s for contact %s", status, contact_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                logger.warning(
                    "inbound_status property may not exist in HubSpot. "
                    "Create it in Settings → Objects → Contacts → Properties."
                )
            raise

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

    def update_inbound_status_sync(self, contact_id: str, status: str) -> None:
        """Synchronous version of update_inbound_status."""
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers, timeout=30.0) as client:
            r = client.patch(
                f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}",
                json={"properties": {"inbound_status": status}},
            )
        if r.status_code == 400:
            logger.warning(
                "inbound_status property may not exist in HubSpot. "
                "Create it in Settings → Objects → Contacts → Properties."
            )
        r.raise_for_status()
        logger.info("Updated inbound_status=%s for contact %s", status, contact_id)

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

    def get_latest_form_submission(self, contact_id: str) -> str | None:
        """Fetch the most recent form submission text for a contact."""
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers, timeout=30.0) as client:
            r = client.get(
                f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}",
                params={"properties": "hs_latest_source_data_2,hs_latest_source"},
            )
            if r.status_code != 200:
                return None
            props = r.json().get("properties", {})
            if props.get("hs_latest_source") != "FORM_SUBMISSION":
                return None
            # hs_latest_source_data_2 holds the form submission message
            return _html_to_text(props.get("hs_latest_source_data_2") or None)

    def get_latest_inbound_email(self, contact_id: str) -> str | None:
        """Fetch body of the most recent inbound email for a contact."""
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers, timeout=30.0) as client:
            r = client.get(
                f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}/associations/emails",
                params={"limit": 5},
            )
            if r.status_code != 200:
                return None
            assoc_results = r.json().get("results", [])

            for item in assoc_results:
                email_id = str(item.get("id", ""))
                if not email_id:
                    continue
                er = client.get(
                    f"{BASE_URL}/crm/v3/objects/emails/{email_id}",
                    params={"properties": "hs_email_direction,hs_email_text,hs_email_subject"},
                )
                if er.status_code != 200:
                    continue
                ep = er.json().get("properties", {})
                if ep.get("hs_email_direction") == "INCOMING_EMAIL":
                    return _html_to_text(ep.get("hs_email_text") or ep.get("hs_email_subject") or None)
        return None

    def get_latest_note(self, contact_id: str) -> str | None:
        """Fetch body of the most recent note associated with a contact."""
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers, timeout=30.0) as client:
            r = client.get(
                f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}/associations/notes",
                params={"limit": 1},
            )
            if r.status_code != 200:
                return None
            assoc_results = r.json().get("results", [])
            if not assoc_results:
                return None
            note_id = str(assoc_results[0].get("id", ""))
            if not note_id:
                return None
            nr = client.get(
                f"{BASE_URL}/crm/v3/objects/notes/{note_id}",
                params={"properties": "hs_note_body"},
            )
            if nr.status_code != 200:
                return None
            return _html_to_text(nr.json().get("properties", {}).get("hs_note_body") or None)

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

    # ------ Ticket API (inbound ticket workflow) ------

    _TICKET_PROPERTIES = (
        "subject,content,hs_pipeline_stage,hs_ticket_priority,source_type,createdate"
    )

    def _ticket_from_api(self, item: dict, primary_contact_id: str | None = None) -> TicketDTO:
        props = item.get("properties", {})
        created_raw = props.get("createdate")
        created_at = None
        if created_raw:
            try:
                created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except ValueError:
                created_at = None
        return TicketDTO(
            id=str(item["id"]),
            subject=props.get("subject"),
            content=_html_to_text(props.get("content")),
            pipeline_stage=props.get("hs_pipeline_stage"),
            priority=props.get("hs_ticket_priority"),
            source_type=props.get("source_type"),
            created_at=created_at,
            primary_contact_id=primary_contact_id,
        )

    def get_ticket_sync(self, ticket_id: str) -> TicketDTO:
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers, timeout=30.0) as client:
            r = client.get(
                f"{BASE_URL}/crm/v3/objects/tickets/{ticket_id}",
                params={"properties": self._TICKET_PROPERTIES},
            )
        r.raise_for_status()
        return self._ticket_from_api(r.json())

    def get_ticket_primary_contact_sync(self, ticket_id: str) -> str | None:
        """First associated contact id, or None if the ticket has no contact."""
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers, timeout=30.0) as client:
            r = client.get(
                f"{BASE_URL}/crm/v3/objects/tickets/{ticket_id}/associations/contacts",
                params={"limit": 1},
            )
        if r.status_code != 200:
            return None
        results = r.json().get("results", [])
        if not results:
            return None
        return str(results[0].get("id") or "") or None

    def update_ticket_stage_sync(self, ticket_id: str, stage_id: str) -> None:
        """Move a ticket to a different pipeline stage. Raises on HTTP error."""
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers, timeout=30.0) as client:
            r = client.patch(
                f"{BASE_URL}/crm/v3/objects/tickets/{ticket_id}",
                json={"properties": {"hs_pipeline_stage": stage_id}},
            )
        r.raise_for_status()

    def search_tickets_sync(
        self,
        created_after: datetime,
        pipeline_stage: str | None = None,
        limit: int = 100,
    ) -> list[TicketDTO]:
        """Tickets created after a given timestamp. Same shape as search_contacts_sync."""
        headers = {"Authorization": f"Bearer {self.token}"}
        ts_ms = str(int(created_after.timestamp() * 1000))
        filters: list[dict] = [
            {"propertyName": "createdate", "operator": "GT", "value": ts_ms},
        ]
        if pipeline_stage:
            filters.append(
                {"propertyName": "hs_pipeline_stage", "operator": "EQ", "value": pipeline_stage}
            )
        body = {
            "filterGroups": [{"filters": filters}],
            "sorts": [{"propertyName": "createdate", "direction": "ASCENDING"}],
            "properties": self._TICKET_PROPERTIES.split(","),
            "limit": limit,
        }
        with httpx.Client(headers=headers, timeout=30.0) as client:
            r = client.post(f"{BASE_URL}/crm/v3/objects/tickets/search", json=body)
        r.raise_for_status()
        return [self._ticket_from_api(item) for item in r.json().get("results", [])]
