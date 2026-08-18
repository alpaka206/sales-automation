"""HubSpot CRM v3 async client."""

from __future__ import annotations

import asyncio
import html as _html
import logging
import random
import re
import time
from datetime import datetime, timezone

import httpx

from ..common.config import settings
from ..common.safe_mode import guard_external_write
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
            logger.warning(
                "HubSpot %s %s transport error (attempt %d): %s", method, url, attempt + 1, exc
            )
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
            method,
            url,
            response.status_code,
            attempt + 1,
            wait,
        )
        await asyncio.sleep(wait)
        delay = min(delay * 2, 30)

    return response  # type: ignore[return-value]


# Private apps allow ~100 requests / 10s. A bulk walk issues its calls back to back
# and will trip that within a couple of seconds, so pace them as well as retry.
_BULK_PACE_SECONDS = 0.12


def _sync_request_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    **kwargs,
) -> httpx.Response:
    """Blocking twin of :func:`_request_with_retries`.

    The bulk backfill walks ~30 pages of tickets and several contact batches on a
    sync client; without this a single 429 mid-walk aborted the whole run (and the
    backfill is not resumable, so it restarted from page 1 every time).
    """
    delay = 1.0
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = client.request(method, url, **kwargs)
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as exc:
            if attempt == _MAX_RETRIES:
                raise
            logger.warning(
                "HubSpot %s %s transport error (attempt %d): %s", method, url, attempt + 1, exc
            )
            time.sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 2, 30)
            continue

        if response.status_code not in _RETRY_STATUS:
            return response
        if attempt == _MAX_RETRIES:
            return response

        retry_after = response.headers.get("retry-after")
        try:
            wait = float(retry_after) if retry_after else delay
        except ValueError:
            wait = delay
        wait += random.uniform(0, 0.5)
        logger.warning(
            "HubSpot %s %s returned %d (attempt %d), retrying in %.1fs",
            method, url, response.status_code, attempt + 1, wait,
        )
        time.sleep(wait)
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


def _contact_properties() -> str:
    names = [
        "email",
        "firstname",
        "lastname",
        "company",
        "phone",
        "country",
        # Where the visitor actually browsed from. `country` is whatever they typed into
        # a form (often blank); this one HubSpot derives from the IP and it is the value
        # the workbook's IP Country column means.
        "hs_ip_country",
        "lifecyclestage",
    ]
    return ",".join(names)


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
        params = {"properties": _contact_properties()}
        if "@" in id_or_email:
            url = f"/crm/v3/objects/contacts/{id_or_email}"
            params["idProperty"] = "email"
        else:
            url = f"/crm/v3/objects/contacts/{id_or_email}"

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
            phone=props.get("phone"),
            country=props.get("country"),
            ip_country=props.get("hs_ip_country"),
            lifecyclestage=props.get("lifecyclestage"),
        )

    async def update_contact(self, contact_id: str, properties: dict) -> None:
        """Update a contact's properties."""
        guard_external_write("hubspot:update_contact")
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

    async def create_email_engagement(
        self,
        contact_id: str,
        subject: str,
        body: str,
        sent_at: datetime | None = None,
        ticket_id: str | None = None,
    ) -> str:
        """Log an email engagement and return its id.

        HubSpot has no API that SENDS this reply (the transactional single-send needs a
        paid add-on and a designed template), so this CRM email object IS the history:
        SMTP delivers, and this records what went out.

        Associated with the contact (type 198) and, when the thread has one, with the
        TICKET (type 224). The ticket association is what the operator actually reads —
        without it a reply lands on the contact timeline only, and the 문의 it answers
        shows no activity at all.
        """
        guard_external_write("hubspot:create_email_engagement")
        http = await self._http()
        ts = int((sent_at or datetime.now(timezone.utc)).timestamp() * 1000)
        from .email_html import to_html_email

        payload = {
            "properties": {
                "hs_timestamp": str(ts),
                "hubspot_owner_id": settings.HUBSPOT_OWNER_ID or None,
                "hs_email_direction": "EMAIL",
                "hs_email_subject": subject,
                "hs_email_text": body,
                "hs_email_html": to_html_email(body),
                "hs_email_status": "SENT",
            },
        }
        r = await http.post("/crm/v3/objects/emails", json=payload)
        r.raise_for_status()
        email_id = r.json()["id"]

        association = await http.put(
            f"/crm/v3/objects/emails/{email_id}/associations/contacts/{contact_id}/198",
        )
        association.raise_for_status()

        if ticket_id:
            # Best effort on purpose: the engagement already exists and is worth keeping
            # even if this fails, and a raise here would send the caller down its
            # "logging failed" path after a mail that really did go out.
            try:
                ticket_link = await http.put(
                    f"/crm/v3/objects/emails/{email_id}/associations/tickets/{ticket_id}/224",
                )
                ticket_link.raise_for_status()
            except Exception:
                logger.warning(
                    "Email engagement %s was logged but could not be attached to ticket %s.",
                    email_id,
                    ticket_id,
                    exc_info=True,
                )

        logger.info(
            "Logged email engagement %s for contact %s (ticket %s)",
            email_id,
            contact_id,
            ticket_id or "-",
        )
        return email_id

    # ------ Sync helpers (for use in synchronous agent code) ------

    def update_inbound_status_sync(self, contact_id: str, status: str) -> None:
        """Synchronous version of update_inbound_status."""
        guard_external_write("hubspot:update_inbound_status")
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
        params = {"properties": _contact_properties()}
        if "@" in id_or_email:
            url = f"{BASE_URL}/crm/v3/objects/contacts/{id_or_email}"
            params["idProperty"] = "email"
        else:
            url = f"{BASE_URL}/crm/v3/objects/contacts/{id_or_email}"

        with httpx.Client(
            headers={"Authorization": f"Bearer {self.token}"}, timeout=30.0
        ) as client:
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
            ip_country=props.get("hs_ip_country"),
            lifecyclestage=props.get("lifecyclestage"),
        )

    def list_tickets_with_contacts_sync(
        self, pipeline: str | None = None, page_limit: int = 100
    ) -> list[tuple[TicketDTO, list[str]]]:
        """Every ticket plus its associated contact ids, as (ticket, contact_ids).

        Uses the LIST endpoint rather than search because only list can return
        associations inline (``associations=contacts``). That trades a slightly
        larger scan — every pipeline, ~29 pages for this portal — for one call per
        page instead of one association GET per ticket, and it avoids
        ``get_ticket_primary_contact_sync``'s ``limit=1`` (which drops the second
        contact on multi-contact tickets) and its habit of reporting a 429 as
        "no contact". ``pipeline`` filters client-side, after the fetch.

        Raises on any non-200 so a rate-limited page fails the run loudly instead
        of silently yielding a short list.
        """
        headers = {"Authorization": f"Bearer {self.token}"}
        out: list[tuple[TicketDTO, list[str]]] = []
        after: str | None = None
        with httpx.Client(headers=headers, timeout=60.0) as client:
            while True:
                params: dict[str, str | int] = {
                    "limit": page_limit,
                    "properties": self._TICKET_PROPERTIES,
                    "associations": "contacts",
                }
                if after:
                    params["after"] = after
                r = _sync_request_with_retries(
                    client, "GET", f"{BASE_URL}/crm/v3/objects/tickets", params=params
                )
                r.raise_for_status()
                page = r.json()
                for item in page.get("results", []):
                    ticket = self._ticket_from_api(item)
                    if pipeline and ticket.pipeline != pipeline:
                        continue
                    ids = [
                        str(a["id"])
                        for a in item.get("associations", {})
                        .get("contacts", {})
                        .get("results", [])
                        if a.get("id")
                    ]
                    out.append((ticket, ids))
                after = page.get("paging", {}).get("next", {}).get("after")
                if not after:
                    break
                time.sleep(_BULK_PACE_SECONDS)
        return out

    def get_contacts_batch_sync(self, contact_ids: list[str]) -> dict[str, ContactDTO]:
        """Fetch many contacts in 100-id batches, keyed by id.

        A backfill would otherwise issue one GET per ticket. Ids that no longer
        exist are simply absent from the result — HubSpot returns 207 with the
        survivors rather than failing the whole batch, so a deleted contact costs
        us that one row instead of the run.
        """
        out: dict[str, ContactDTO] = {}
        if not contact_ids:
            return out
        props = _contact_properties().split(",")
        headers = {"Authorization": f"Bearer {self.token}"}
        unique = list(dict.fromkeys(str(c) for c in contact_ids if c))
        with httpx.Client(headers=headers, timeout=60.0) as client:
            for start in range(0, len(unique), 100):
                if start:
                    time.sleep(_BULK_PACE_SECONDS)
                chunk = unique[start : start + 100]
                r = _sync_request_with_retries(
                    client,
                    "POST",
                    f"{BASE_URL}/crm/v3/objects/contacts/batch/read",
                    json={"properties": props, "inputs": [{"id": cid} for cid in chunk]},
                )
                if r.status_code not in (200, 207):
                    raise HubSpotAPIError(
                        f"contacts batch read failed ({r.status_code}): {r.text[:200]}"
                    )
                for item in r.json().get("results", []):
                    p = item.get("properties", {}) or {}
                    out[str(item["id"])] = ContactDTO(
                        id=str(item["id"]),
                        email=p.get("email"),
                        firstname=p.get("firstname"),
                        lastname=p.get("lastname"),
                        company=p.get("company"),
                        phone=p.get("phone"),
                        country=p.get("country"),
                        ip_country=p.get("hs_ip_country"),
                        lifecyclestage=p.get("lifecyclestage"),
                    )
        return out

    def existing_ticket_ids_sync(self, ticket_ids: list[str]) -> set[str]:
        """Which of these ticket ids HubSpot still has, in 100-id batches.

        Absence is the answer we are after, so this returns ids rather than tickets:
        a batch read answers "is it gone?" for a hundred tickets in one call, where
        ``get_ticket_sync`` answers it for one and needs a 404 to do it. HubSpot
        returns 207 with the survivors and leaves the rest out — deleted tickets are
        archived, and an archived object is absent from a plain read.

        Raises rather than returning a short set when a batch fails: a caller that
        deletes what is missing must never read "the token expired" as "all gone".

        **404 is the exception, and it is the case this exists for.** HubSpot does not
        always answer a partially-missing batch with 207 — it can refuse the whole read
        with 404 ("Could not get some TICKET objects, they may be deleted"), which is
        precisely the answer we were asking for. Treating that as a failed batch skipped
        the deletion pass every time there was something to delete, so a deleted ticket
        stayed on the board no matter how often 최신화 was pressed. That chunk is asked
        again one at a time, where a 404 is unambiguous.
        """
        found: set[str] = set()
        unique = list(dict.fromkeys(str(t) for t in ticket_ids if t))
        if not unique:
            return found
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers, timeout=60.0) as client:
            for start in range(0, len(unique), 100):
                if start:
                    time.sleep(_BULK_PACE_SECONDS)
                chunk = unique[start : start + 100]
                r = _sync_request_with_retries(
                    client,
                    "POST",
                    f"{BASE_URL}/crm/v3/objects/tickets/batch/read",
                    json={"properties": ["hs_object_id"], "inputs": [{"id": t} for t in chunk]},
                )
                if r.status_code == 404:
                    found.update(self._existing_one_by_one(client, chunk))
                    continue
                if r.status_code not in (200, 207):
                    raise HubSpotAPIError(
                        f"tickets batch read failed ({r.status_code}): {r.text[:200]}"
                    )
                found.update(str(item["id"]) for item in r.json().get("results", []))
        return found

    def _existing_one_by_one(self, client: httpx.Client, ticket_ids: list[str]) -> set[str]:
        """Which of these exist, one GET each. The slow path behind a refused batch.

        Only 404/410 counts as absent. Anything else raises — a chunk that fails because
        of the token must not come back as "these hundred are gone".
        """
        alive: set[str] = set()
        for ticket_id in ticket_ids:
            r = _sync_request_with_retries(
                client, "GET", f"{BASE_URL}/crm/v3/objects/tickets/{ticket_id}"
            )
            if r.status_code == 200:
                alive.add(str(ticket_id))
            elif r.status_code not in (404, 410):
                raise HubSpotAPIError(
                    f"ticket {ticket_id} existence check failed ({r.status_code}): {r.text[:200]}"
                )
        return alive

    async def create_interaction_note(
        self,
        contact_id: str,
        body: str,
        happened_at: datetime | None = None,
        ticket_id: str | None = None,
    ) -> str:
        """Put one 소통 히스토리 on the contact's HubSpot timeline, as a note.

        A note, and ONE object type for all ten of the console's channels — not
        hs_call / hs_meeting / hs_communication. What the operator files is a whole
        exchange summarized once ("전화로 단가 재확인, 검토 후 회신하기로"), which is
        not what a call object's duration, direction and status columns are for; the
        channel goes on the note's first line instead. Three more object types buy
        HubSpot-side REPORTING on calls, and nothing at all for reading the timeline —
        so they can earn their own path on the day somebody asks to filter by it.

        Associations use the v4 default endpoint, so no association type id is spelled
        out here and none can be spelled wrong.
        """
        guard_external_write("hubspot:create_interaction_note")
        http = await self._http()
        ts = int((happened_at or datetime.now(timezone.utc)).timestamp() * 1000)
        r = await http.post(
            "/crm/v3/objects/notes",
            json={
                "properties": {
                    "hs_timestamp": str(ts),
                    "hubspot_owner_id": settings.HUBSPOT_OWNER_ID or None,
                    "hs_note_body": body,
                }
            },
        )
        r.raise_for_status()
        note_id = r.json()["id"]

        link = await http.put(
            f"/crm/v4/objects/notes/{note_id}/associations/default/contacts/{contact_id}"
        )
        link.raise_for_status()

        if ticket_id:
            # Best effort, same as the email engagement: the note already exists and is
            # worth keeping even if it ends up on the contact timeline only.
            try:
                ticket_link = await http.put(
                    f"/crm/v4/objects/notes/{note_id}/associations/default/tickets/{ticket_id}"
                )
                ticket_link.raise_for_status()
            except Exception:
                logger.warning(
                    "Note %s was logged but could not be attached to ticket %s.",
                    note_id, ticket_id, exc_info=True,
                )

        logger.info("Logged interaction note %s for contact %s", note_id, contact_id)
        return note_id

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
                    params={
                        "properties": (
                            "hs_email_subject,hs_email_text,hs_email_timestamp,hs_timestamp,"
                            "hs_email_direction"
                        )
                    },
                )
                if er.status_code != 200:
                    continue
                ep = er.json().get("properties", {})
                engagements.append(
                    EngagementDTO(
                        id=email_id,
                        type=(ep.get("hs_email_direction") or "email").lower(),
                        subject=ep.get("hs_email_subject"),
                        body=ep.get("hs_email_text"),
                        timestamp=(
                            datetime.fromisoformat(
                                ep.get("hs_email_timestamp") or ep["hs_timestamp"]
                            )
                            if ep.get("hs_email_timestamp") or ep.get("hs_timestamp")
                            else None
                        ),
                    )
                )
        return engagements

    # What a person logs in HubSpot by hand, per object type: (channel we file it under,
    # title property, body property, time property). Emails are NOT here — they have
    # their own reader above, and this app writes them.
    _LOGGED_ENGAGEMENTS = {
        "calls": ("phone", "hs_call_title", "hs_call_body", "hs_timestamp"),
        "meetings": ("meeting", "hs_meeting_title", "hs_meeting_body", "hs_meeting_start_time"),
        "communications": ("manual", None, "hs_communication_body", "hs_timestamp"),
    }

    def get_logged_engagements_sync(
        self, contact_id: str, limit: int = 20
    ) -> list[tuple[str, EngagementDTO]]:
        """(channel, engagement) for the calls, meetings and messages logged in HubSpot.

        The other half of the history. Somebody presses "Log a call" in HubSpot and this
        console showed nothing — and the 리드 히스토리 screen is the one that claims to
        hold everything, so a missing record reads as "no contact since", not as "look
        somewhere else".

        One bad object type must not cost the other two: HubSpot returns 400 for a type
        the portal does not have enabled, and this is a background sync.
        """
        out: list[tuple[str, EngagementDTO]] = []
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers, timeout=30.0) as client:
            for object_type, (channel, title_prop, body_prop, time_prop) in (
                self._LOGGED_ENGAGEMENTS.items()
            ):
                try:
                    r = client.get(
                        f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}"
                        f"/associations/{object_type}",
                        params={"limit": limit},
                    )
                    if r.status_code != 200:
                        continue
                    props = ",".join(p for p in (title_prop, body_prop, time_prop) if p)
                    for item in r.json().get("results", []):
                        object_id = str(item.get("id", ""))
                        if not object_id:
                            continue
                        detail = client.get(
                            f"{BASE_URL}/crm/v3/objects/{object_type}/{object_id}",
                            params={"properties": props},
                        )
                        if detail.status_code != 200:
                            continue
                        p = detail.json().get("properties", {}) or {}
                        raw_time = p.get(time_prop) or p.get("hs_timestamp")
                        out.append((
                            channel,
                            EngagementDTO(
                                id=f"{object_type}:{object_id}",
                                type=object_type,
                                subject=_html_to_text(p.get(title_prop)) if title_prop else None,
                                body=_html_to_text(p.get(body_prop)),
                                timestamp=(
                                    datetime.fromisoformat(raw_time) if raw_time else None
                                ),
                            ),
                        ))
                except Exception:
                    logger.warning(
                        "Could not read %s for contact %s", object_type, contact_id, exc_info=True
                    )
        return out

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
                    return _html_to_text(
                        ep.get("hs_email_text") or ep.get("hs_email_subject") or None
                    )
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
                deals.append(
                    DealDTO(
                        id=deal_id,
                        name=dp.get("dealname"),
                        stage=dp.get("dealstage"),
                        amount=dp.get("amount"),
                    )
                )
        return deals

    # ------ Ticket API (inbound ticket workflow) ------

    _TICKET_PROPERTIES = (
        "subject,content,hs_pipeline,hs_pipeline_stage,hs_ticket_priority,"
        "source_type,createdate,hs_lastmodifieddate,hs_all_associated_contact_emails"
    )

    def _ticket_from_api(self, item: dict, primary_contact_id: str | None = None) -> TicketDTO:
        props = item.get("properties", {})
        created_raw = props.get("createdate")
        updated_raw = props.get("hs_lastmodifieddate")
        created_at = None
        updated_at = None
        if created_raw:
            try:
                created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except ValueError:
                created_at = None
        if updated_raw:
            try:
                updated_at = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
            except ValueError:
                updated_at = None
        return TicketDTO(
            id=str(item["id"]),
            subject=props.get("subject"),
            content=_html_to_text(props.get("content")),
            pipeline=props.get("hs_pipeline"),
            pipeline_stage=props.get("hs_pipeline_stage"),
            priority=props.get("hs_ticket_priority"),
            source_type=props.get("source_type"),
            created_at=created_at,
            updated_at=updated_at,
            primary_contact_id=primary_contact_id,
            contact_emails=props.get("hs_all_associated_contact_emails"),
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
        guard_external_write("hubspot:update_ticket_stage")
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
        pipeline: str | None = None,
    ) -> list[TicketDTO]:
        """Tickets changed after a timestamp, following HubSpot search pages.

        Searching the modification timestamp also catches tickets created in
        another stage and later moved into the configured New stage.

        ``pipeline`` narrows to one pipeline across ALL its stages — that is what a
        backfill wants, whereas ``pipeline_stage`` pins a single stage. The
        timestamp filter must stay first: tests/test_hubspot.py pins filters[0].
        """
        headers = {"Authorization": f"Bearer {self.token}"}
        ts_ms = str(int(created_after.timestamp() * 1000))
        filters: list[dict] = [
            {"propertyName": "hs_lastmodifieddate", "operator": "GT", "value": ts_ms},
        ]
        if pipeline:
            filters.append(
                {"propertyName": "hs_pipeline", "operator": "EQ", "value": pipeline}
            )
        if pipeline_stage:
            filters.append(
                {"propertyName": "hs_pipeline_stage", "operator": "EQ", "value": pipeline_stage}
            )
        tickets: list[TicketDTO] = []
        after: str | None = None
        with httpx.Client(headers=headers, timeout=30.0) as client:
            while len(tickets) < limit:
                body = {
                    "filterGroups": [{"filters": filters}],
                    "sorts": [
                        {"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}
                    ],
                    "properties": self._TICKET_PROPERTIES.split(","),
                    "limit": min(100, limit - len(tickets)),
                }
                if after:
                    body["after"] = after
                response = client.post(f"{BASE_URL}/crm/v3/objects/tickets/search", json=body)
                response.raise_for_status()
                page = response.json()
                tickets.extend(self._ticket_from_api(item) for item in page.get("results", []))
                after = page.get("paging", {}).get("next", {}).get("after")
                if not after:
                    break
        return tickets


def move_ticket_stage_after_send(ticket_id: str | None) -> bool:
    """Best-effort: move a ticket to settings.HUBSPOT_TICKET_STAGE_AFTER_SEND.

    Shared by the approval endpoint (mark_sent) and the send worker so the
    post-send ticket-stage transition lives in one place. Never raises — the
    email already went out, so a HubSpot failure here must not reverse the send.
    """
    target = settings.HUBSPOT_TICKET_STAGE_AFTER_SEND
    if not ticket_id or not target:
        return True
    try:
        HubSpotClient().update_ticket_stage_sync(ticket_id, target)
        succeeded = True
        logger.info("Moved ticket %s → stage %s after send.", ticket_id, target)
    except HubSpotNotConfigured:
        succeeded = False
        logger.warning("HubSpot not configured; cannot move ticket %s stage.", ticket_id)
    except Exception:
        succeeded = False
        logger.exception("Ticket stage update failed (ticket=%s). Send succeeded.", ticket_id)
    return succeeded
