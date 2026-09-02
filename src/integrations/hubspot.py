"""HubSpot CRM v3 async client."""

from __future__ import annotations

import asyncio
import html as _html
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from ..common.config import settings
from ..common.safe_mode import guard_external_write
from .hubspot_models import ContactDTO, DealDTO, EngagementDTO, TicketDTO
from .delivery import DeliveryPermanentError, DeliveryTransientError, DeliveryUnknown

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


@dataclass(frozen=True)
class ConversationReplyContext:
    """The existing HubSpot thread and connected email account used for a reply."""

    thread_id: str
    channel_id: str
    channel_account_id: str


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


def _parse_ts(raw: object) -> datetime | None:
    """허브스팟의 ISO 시각을 datetime 으로. 못 읽으면 None — 시각 하나 때문에 스윕이 서면 안 된다."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


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
        #
        # **이 포털의 이름은 `ip_country` 다** — `hs_` 접두사가 붙은 쪽은 아예 없다(2026-08-26
        # 실측). 오래 없는 이름만 물어보고 있었고, 허브스팟은 모르는 속성을 조용히 무시하므로
        # 값이 늘 비어 있어도 아무도 몰랐다. 둘 다 적어 둔다: 포털마다 다를 수 있고, 없는
        # 이름은 그냥 안 돌아온다.
        "ip_country",
        "hs_ip_country",
        "lifecyclestage",
        # 고객 상태 카드가 읽는 셋. 이름을 여기 박아도 되는 이유: 허브스팟은 **모르는 속성
        # 이름을 조용히 무시한다**(400 이 아니다 — 2026-08-26 실측). 그래서 포털에 그 속성이
        # 없으면 값이 안 올 뿐, 이 호출이 깨지지 않는다.
        "plan",
        "plan_tier",
        "plan_seq",
        "user_seq",
        "space_seq",
        "industry",
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
            ip_country=props.get("ip_country") or props.get("hs_ip_country"),
            lifecyclestage=props.get("lifecyclestage"),
            plan=props.get("plan"),
            plan_tier=props.get("plan_tier"),
            plan_seq=props.get("plan_seq"),
            user_seq=props.get("user_seq"),
            space_seq=props.get("space_seq"),
            industry=props.get("industry"),
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

    @staticmethod
    def _delivery_addresses(message: dict) -> set[str]:
        """Collect email delivery identifiers from both legacy response shapes."""
        values: set[str] = set()
        for party in [*(message.get("senders") or []), *(message.get("recipients") or [])]:
            identifiers = party.get("deliveryIdentifiers") or []
            if party.get("deliveryIdentifier"):
                identifiers = [party["deliveryIdentifier"]]
            for identifier in identifiers:
                if not isinstance(identifier, dict):
                    continue
                if identifier.get("type") != "HS_EMAIL_ADDRESS":
                    continue
                value = str(identifier.get("value") or "").strip().lower()
                if value:
                    values.add(value)
        return values

    @staticmethod
    def _lookup_error(response: httpx.Response, action: str) -> RuntimeError:
        """Turn a HubSpot error response into our delivery exception.

        **The reason lives in ``errors``, not in ``message``.** A HubSpot validation
        failure answers with ``"message": "Multiple errors validating request."`` — the
        same sentence for every cause — and puts what actually failed in an ``errors``
        array. Reading only ``message`` left a log line that said something was wrong and
        never what, so a failed send could not be diagnosed from the logs at all; it cost
        a live send to find out. Both are kept: the summary names the shape, the array
        names the field.
        """
        detail = ""
        try:
            payload = response.json()
            detail = str(payload.get("message") or "")
            reasons = [
                str(item.get("message") or "").strip()
                for item in (payload.get("errors") or [])
                if isinstance(item, dict)
            ]
            joined = "; ".join(reason for reason in reasons if reason)
            if joined:
                detail = f"{detail} [{joined}]" if detail else joined
            detail = detail[:600]
        except Exception:
            detail = response.text[:600]
        message = f"HubSpot {action} failed (HTTP {response.status_code})"
        if detail:
            message += f": {detail}"
        if response.status_code == 429 or response.status_code >= 500:
            return DeliveryTransientError(message)
        return DeliveryPermanentError(message)

    async def _get_conversation_json(
        self, url: str, *, params: dict | None = None, action: str
    ) -> dict:
        """GET Conversations data with safe retries and delivery-oriented errors."""
        try:
            response = await self._retry("GET", url, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise DeliveryTransientError(f"HubSpot {action} transport failure: {exc}") from exc
        if response.status_code >= 400:
            raise self._lookup_error(response, action)
        return response.json()

    async def _email_channel_account(self, account_id: str) -> dict:
        data = await self._get_conversation_json(
            f"/conversations/v3/conversations/channel-accounts/{account_id}",
            action="channel-account lookup",
        )
        if str(data.get("channelId") or "") != "1002":
            raise DeliveryPermanentError(
                f"HubSpot channel account {account_id} is not an email channel"
            )
        if data.get("archived") or not data.get("active") or not data.get("authorized"):
            raise DeliveryPermanentError(
                f"HubSpot email channel account {account_id} is inactive or unauthorized"
            )
        return data

    async def validate_conversation_sender(self) -> dict:
        """Verify that the configured sender is an active HubSpot agent actor."""
        actor_id = settings.HUBSPOT_SENDER_ACTOR_ID.strip()
        if not actor_id.startswith("A-"):
            raise DeliveryPermanentError(
                "HUBSPOT_SENDER_ACTOR_ID must be an agent actor such as A-82843387"
            )
        actor = await self._get_conversation_json(
            f"/conversations/v3/conversations/actors/{actor_id}",
            action="sender actor lookup",
        )
        if actor.get("type") != "AGENT" or str(actor.get("id") or "") != actor_id:
            raise DeliveryPermanentError(
                f"HubSpot actor {actor_id} is not a valid AGENT sender"
            )
        return actor

    async def find_conversation_reply_context(
        self, ticket_id: str, recipient_email: str, *, preferred_account_id: str = ""
    ) -> ConversationReplyContext:
        """Select the newest safe email reply route for a ticket.

        Existing outbound/inbound email messages are the source of truth for channel
        account selection. A form-only thread has no email route to copy, so it may use
        the configured default email account only when that account belongs to the same
        inbox. Ambiguous tickets fail closed instead of replying on the wrong thread.

        ``preferred_account_id`` 는 **운영자가 고른 발신 주소**입니다(이관 0105). 비어 있으면
        예전 그대로 — 스레드가 정합니다. 값이 있으면 스레드는 아래 규칙이 똑같이 고르고
        **계정만** 그것으로 바뀝니다: 어느 스레드에 붙일지는 운영자가 판단할 일이 아니고,
        엉뚱한 스레드에 답이 붙으면 고객이 보는 대화가 두 갈래가 됩니다.

        고른 계정은 **그 스레드의 인박스에 속해야** 합니다 — 아래 폼 폴백이 이미 지키는
        규칙과 같은 규칙입니다. 인박스가 다르면 HubSpot 이 받아 줄지 알 수 없고, 받아
        준다면 그건 그것대로 남의 인박스에 우리 메일이 서는 것입니다. 실패는 **닫는 쪽**
        으로 냅니다.
        """
        target = recipient_email.strip().lower()
        if not ticket_id:
            raise DeliveryPermanentError("The message has no HubSpot ticket ID")
        if not target or "@" not in target:
            raise DeliveryPermanentError("The message has no valid recipient email")

        data = await self._get_conversation_json(
            "/conversations/v3/conversations/threads",
            params={
                "associatedTicketId": ticket_id,
                "association": "TICKET",
                "limit": 100,
            },
            action="ticket thread lookup",
        )
        threads = [
            thread
            for thread in (data.get("results") or [])
            if not thread.get("archived") and not thread.get("spam")
        ]
        if not threads:
            raise DeliveryPermanentError(
                f"HubSpot ticket {ticket_id} has no active Conversations thread"
            )

        # 후보에 **스레드의 인박스**도 같이 싣습니다. 고른 발신 주소가 있을 때 「그 주소를
        # 쓸 수 있는 스레드」를 고르려면 인박스를 알아야 합니다.
        candidates: list[tuple[str, str, str, str, str]] = []
        target_threads: list[dict] = []
        for thread in threads:
            thread_id = str(thread.get("id") or "")
            if not thread_id:
                continue
            messages_data = await self._get_conversation_json(
                f"/conversations/v3/conversations/threads/{thread_id}/messages",
                params={"limit": 100},
                action=f"thread {thread_id} message lookup",
            )
            messages = [
                message
                for message in (messages_data.get("results") or [])
                if message.get("type") == "MESSAGE"
            ]
            if any(target in self._delivery_addresses(message) for message in messages):
                target_threads.append(thread)
            for message in messages:
                if target not in self._delivery_addresses(message):
                    continue
                channel_id = str(message.get("channelId") or "")
                account_id = str(message.get("channelAccountId") or "")
                if channel_id != "1002" or not account_id:
                    continue
                timestamp = str(message.get("createdAt") or "")
                candidates.append((
                    timestamp, thread_id, channel_id, account_id,
                    str(thread.get("inboxId") or ""),
                ))

        # **없어진 채널 계정을 만나면 다음 후보로 넘어갑니다** (2026-08-31).
        #
        # 스레드에 남은 옛 메시지가 지금은 존재하지 않는 채널 계정을 가리키는 일이 흔합니다 —
        # 담당자가 바뀌면서 연결이 끊긴 개인 메일함이 그렇습니다. 예전에는 **가장 최근 후보
        # 하나만** 검증했고, 그 조회가 404 면 `_lookup_error` 가 영구 실패를 던져 아래 폴백에
        # 닿지도 못했습니다. 운영 티켓 329건에 이 규칙을 그대로 돌려 보면 **211건이 그
        # 상태**였습니다(won 2 · negotiation 1 포함) — 발송을 누르는 순간
        # 「channel-account lookup failed (HTTP 404)」로 죽습니다.
        #
        # 후보는 각자 자기 스레드를 들고 있으므로, 넘어가도 인박스를 넘나들지 않습니다.
        wanted = (preferred_account_id or "").strip()
        if wanted:
            return await self._thread_for_chosen_sender(
                ticket_id, wanted, candidates, target_threads or threads
            )
        for _timestamp, thread_id, channel_id, account_id, _inbox in sorted(candidates, reverse=True):
            try:
                await self._email_channel_account(account_id)
            except DeliveryPermanentError as exc:
                # 일시 오류(429·5xx·네트워크)는 여기서 안 잡습니다 — 그건 「이 계정이
                # 못 쓴다」가 아니라 「지금 못 물어봤다」라서, 넘어가면 멀쩡한 계정을
                # 두고 엉뚱한 주소로 나갑니다.
                logger.warning(
                    "채널 계정 %s 는 쓸 수 없어 다음 후보로 넘어갑니다 (ticket=%s): %s",
                    account_id, ticket_id, exc,
                )
                continue
            return ConversationReplyContext(thread_id, channel_id, account_id)

        # A brand-new form thread has no email message to copy yet. Use the configured
        # support channel only when one matching thread is unambiguous and shares its inbox.
        fallback_threads = target_threads or threads
        if len(fallback_threads) != 1:
            raise DeliveryPermanentError(
                f"HubSpot ticket {ticket_id} has {len(fallback_threads)} possible threads "
                "and no prior email route to disambiguate them"
            )
        account_id = settings.HUBSPOT_DEFAULT_EMAIL_CHANNEL_ACCOUNT_ID.strip()
        if not account_id:
            raise DeliveryPermanentError(
                "HUBSPOT_DEFAULT_EMAIL_CHANNEL_ACCOUNT_ID is required for a form-only thread"
            )
        thread = fallback_threads[0]
        account = await self._email_channel_account(account_id)
        if str(account.get("inboxId") or "") != str(thread.get("inboxId") or ""):
            raise DeliveryPermanentError(
                f"HubSpot email channel account {account_id} does not belong to thread inbox"
            )
        return ConversationReplyContext(str(thread["id"]), "1002", account_id)

    async def _thread_for_chosen_sender(
        self,
        ticket_id: str,
        account_id: str,
        candidates: list[tuple[str, str, str, str, str]],
        threads: list[dict],
    ) -> ConversationReplyContext:
        """운영자가 고른 주소로 보낼 수 있는 스레드를 고릅니다.

        **스레드를 고르는 규칙이 뒤집힙니다.** 아무도 안 골랐을 때는 「가장 최근에 이 고객과
        메일이 오간 스레드」가 이기고 계정은 그 스레드가 정합니다. 골랐을 때는 반대로 **계정이
        먼저**이고, 그 계정을 쓸 수 있는 스레드를 찾습니다 — 안 그러면 고른 주소가 그 스레드의
        인박스에 없다는 이유로 늘 거절됩니다(운영 실측: 그렇게 하면 B2B 티켓의 32%만
        `perso.ai@estsoft.com` 으로 나갔습니다).

        찾는 순서는 둘입니다:

        1. **그 인박스에서 이 고객과 이미 메일이 오간 스레드** — 가장 최근 것. 대화가 이어지는
           자리라 제일 안전합니다.
        2. 그런 스레드가 없으면, **그 인박스에 이 티켓의 스레드가 하나뿐일 때만** 그것.
           폼·챗봇으로만 들어와 아직 메일이 오간 적 없는 스레드가 이 경우입니다. 둘 이상이면
           어느 쪽인지 우리가 정할 수 없어 실패합니다 — 엉뚱한 스레드에 답이 붙으면 고객이
           보는 대화가 두 갈래가 됩니다.

        **같은 인박스 규칙은 그대로 겁니다.** 허브스팟이 다른 인박스의 계정을 받아 줄지는
        **문서에도 없고 공개 사례도 없습니다** — 공식 문서는 스레드의 계정을 쓰라고 「권고」할
        뿐이지만, 발송 시점에만 도는 관문이 따로 있습니다
        (`CHANNEL_ACCOUNT_CANNOT_SEND_MESSAGE_ON_THREAD`). 읽기로는 못 가립니다. 받아 준다
        해도 그건 남의 인박스에 우리 메일이 서는 것이고, 고객 답장이 그 팀으로 갑니다.
        """
        account = await self._email_channel_account(account_id)
        inbox_id = str(account.get("inboxId") or "")
        same_inbox = sorted(
            (item for item in candidates if item[4] == inbox_id), reverse=True
        )
        if same_inbox:
            return ConversationReplyContext(same_inbox[0][1], same_inbox[0][2], account_id)
        in_inbox = [t for t in threads if str(t.get("inboxId") or "") == inbox_id]
        if len(in_inbox) == 1:
            return ConversationReplyContext(str(in_inbox[0]["id"]), "1002", account_id)
        if not in_inbox:
            raise DeliveryPermanentError(
                f"고른 발신 주소는 이 티켓({ticket_id})에 쓸 수 없습니다 — 그 주소가 연결된 "
                "인박스에 이 티켓의 대화가 없습니다"
            )
        raise DeliveryPermanentError(
            f"고른 발신 주소로 답할 스레드를 정할 수 없습니다 (티켓 {ticket_id}, 후보 "
            f"{len(in_inbox)}개) — 그 인박스에 이 고객과 오간 메일이 아직 없습니다"
        )

    async def list_reply_senders(self, ticket_id: str, recipient_email: str) -> list[dict]:
        """그 티켓에 **고를 수 있는** 발신 주소들. 읽기만 합니다 — 아무것도 안 보냅니다.

        기본값(아무것도 안 고르면 나갈 주소)을 먼저 정하고, 그 스레드의 인박스에 연결된
        살아 있는 이메일 계정을 전부 돌려줍니다. 화면이 스스로 목록을 만들면 그 목록이
        발송이 실제로 받아 주는 것과 언젠가 갈라집니다 — 여기서 만든 것만 고를 수 있습니다.
        """
        context = await self.find_conversation_reply_context(ticket_id, recipient_email)
        thread = await self._get_conversation_json(
            f"/conversations/v3/conversations/threads/{context.thread_id}",
            action=f"thread {context.thread_id} lookup",
        )
        inbox_id = str(thread.get("inboxId") or "")
        data = await self._get_conversation_json(
            "/conversations/v3/conversations/channel-accounts",
            params={"limit": 200},
            action="channel-account list",
        )
        out: list[dict] = []
        for account in data.get("results") or ():
            if str(account.get("channelId") or "") != "1002":
                continue
            if account.get("archived") or not account.get("active"):
                continue
            if not account.get("authorized"):
                continue
            if str(account.get("inboxId") or "") != inbox_id:
                continue
            address = (account.get("deliveryIdentifier") or {}).get("value") or ""
            out.append({
                "id": str(account.get("id") or ""),
                "address": address or (account.get("name") or ""),
                "is_default": str(account.get("id") or "") == context.channel_account_id,
            })
        out.sort(key=lambda item: (not item["is_default"], item["address"]))
        return out

    async def send_conversation_message(
        self,
        context: ConversationReplyContext,
        *,
        recipient_email: str,
        subject: str,
        text: str,
        rich_text: str,
    ) -> str:
        """Send one reply to an existing thread and return the HubSpot message ID.

        The POST is deliberately attempted once. A timeout, connection break, or 5xx
        response is quarantined as delivery-unknown because HubSpot may already have
        accepted the message and this endpoint has no idempotency key.
        """
        guard_external_write("hubspot:send_conversation_message")
        actor = await self.validate_conversation_sender()
        actor_id = str(actor["id"])
        recipient = recipient_email.strip()
        payload = {
            "type": "MESSAGE",
            "subject": subject,
            "text": text,
            "richText": rich_text,
            "senderActorId": actor_id,
            "channelId": context.channel_id,
            "channelAccountId": context.channel_account_id,
            # 수신자는 **주소로만** 지정합니다 — ``actorId`` 를 넣지 않습니다.
            #
            # HubSpot 문서의 예시에는 ``"actorId": "E-user@hubspot.com"`` 이 있고, actor 조회
            # 엔드포인트도 그 ID 를 200 으로 받아줍니다(``{"type": "EMAIL"}``). 그래서 읽기
            # 검증만으로는 멀쩡해 보입니다. 그런데 **발송 엔드포인트만** 그것을 거부합니다::
            #
            #     Actor type EMAIL is not supported for receiving;
            #     `E-devrel.365@gmail.com` is not a valid actor ID
            #
            # (2026-08-26, msg 62 — 이 포털의 첫 실제 발송이 이것으로 실패했습니다.)
            # 이 포털에서 성공한 발신 28건도 전부 recipients 에 actorId 가 없습니다.
            # 문서가 API 보다 오래됐습니다.
            "recipients": [
                {
                    "recipientField": "TO",
                    "deliveryIdentifiers": [
                        {"type": "HS_EMAIL_ADDRESS", "value": recipient}
                    ],
                }
            ],
        }
        http = await self._http()
        try:
            response = await http.post(
                f"/conversations/v3/conversations/threads/{context.thread_id}/messages",
                json=payload,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise DeliveryUnknown(
                f"HubSpot message delivery outcome is unknown: {exc}"
            ) from exc

        if 200 <= response.status_code < 300:
            message_id = str(response.json().get("id") or "")
            if not message_id:
                raise DeliveryUnknown("HubSpot accepted the message but returned no message ID")
            return message_id
        if response.status_code == 429:
            raise DeliveryTransientError("HubSpot rate-limited the message send (HTTP 429)")
        if response.status_code >= 500:
            raise DeliveryUnknown(
                f"HubSpot message delivery outcome is unknown (HTTP {response.status_code})"
            )
        raise self._lookup_error(response, "conversation message send")

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

    def update_contact_company_sync(self, contact_id: str, company: str) -> None:
        """연락처의 회사 이름을 허브스팟에도 씁니다.

        콘솔의 「연락처 정보」에서 고친 회사 이름이 우리 DB 에만 남던 것을 고칩니다 —
        같은 사람의 회사가 두 화면에서 다르면 어느 쪽이 맞는지 알 방법이 없습니다.

        **회사 이름 한 칸만** 씁니다. 다른 속성은 이 경로로 못 지나가므로, 콘솔에 닿은
        누구든 `email` 이나 `lifecyclestage` 를 덮어쓰는 일은 생기지 않습니다(플랜 다섯
        칸이 `RECORD_FIELDS.editable` 로 울타리를 치는 것과 같은 이유입니다).
        """
        guard_external_write("hubspot:update_contact_company")
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers, timeout=30.0) as client:
            r = client.patch(
                f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}",
                json={"properties": {"company": company}},
            )
        r.raise_for_status()
        logger.info("Updated company for contact %s", contact_id)

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
            ip_country=props.get("ip_country") or props.get("hs_ip_country"),
            lifecyclestage=props.get("lifecyclestage"),
            plan=props.get("plan"),
            plan_tier=props.get("plan_tier"),
            plan_seq=props.get("plan_seq"),
            user_seq=props.get("user_seq"),
            space_seq=props.get("space_seq"),
            industry=props.get("industry"),
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
        """Fetch email engagements with content for a contact (sync), newest side first.

        **연결 목록은 페이지를 끝까지 넘깁니다.** 예전에는 첫 페이지만 읽고 `limit` 을
        그대로 넘겼는데, 그러면 그 수를 넘긴 사람은 **조용히 잘렸습니다** — 실측으로
        허브스팟에 메일 30통이 있는 연락처가 우리 화면에는 20통이었고, 어디에도 「덜
        가져왔다」는 표시가 없었습니다(2026-08-20). 그래서 `limit` 은 이제 **몇 통까지
        본문을 받아올지**만 정합니다. 목록 자체는 전부 세어 봐야 「다 가져왔나」에
        답할 수 있습니다.
        """
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(headers=headers, timeout=30.0) as client:
            assoc_results: list[dict] = []
            after: str | None = None
            while True:
                params: dict[str, object] = {"limit": 500}
                if after:
                    params["after"] = after
                r = client.get(
                    f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}/associations/emails",
                    params=params,
                )
                r.raise_for_status()
                page = r.json()
                assoc_results.extend(page.get("results", []))
                after = ((page.get("paging") or {}).get("next") or {}).get("after")
                if not after:
                    break
            # 연결 목록은 오래된 것부터 옵니다. 본문을 일부만 받아올 때 버릴 쪽은
            # 오래된 쪽입니다 — 「최근 것 좀 당겨오기」가 이 함수의 쓰임입니다.
            if limit and len(assoc_results) > limit:
                assoc_results = assoc_results[-limit:]

            email_ids = [str(i.get("id", "")) for i in assoc_results if i.get("id")]
            # 어느 티켓의 메일인지는 **허브스팟에 적혀 있습니다.** 한 번에 물어봅니다 —
            # 메일마다 따로 물으면 연락처 한 명에 왕복이 두 배가 됩니다.
            ticket_of = self._ticket_of_emails(client, email_ids)

            engagements: list[EngagementDTO] = []
            for email_id in email_ids:
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
                        ticket_id=ticket_of.get(email_id),
                    )
                )
        return engagements

    @staticmethod
    def _ticket_of_emails(client: httpx.Client, email_ids: list[str]) -> dict[str, str]:
        """{email id: ticket id} — 붙어 있는 티켓이 있는 메일만.

        실측(2026-08-20)으로 표본 9건 중 8건에 티켓 연결이 있었습니다. 영업이 티켓에서
        회신하면 허브스팟이 그렇게 답니다. 연결이 여럿이면 첫 번째만 씁니다 — 한 메일이
        두 문의의 답인 경우는 우리 화면에 표현할 자리가 없습니다.

        실패해도 조용히 빕니다: 티켓을 못 붙이는 것과 기록을 통째로 못 가져오는 것 중
        나쁜 쪽은 뒤엣것입니다.
        """
        out: dict[str, str] = {}
        for start in range(0, len(email_ids), 100):  # 배치 상한
            chunk = email_ids[start : start + 100]
            try:
                r = client.post(
                    f"{BASE_URL}/crm/v4/associations/emails/tickets/batch/read",
                    json={"inputs": [{"id": eid} for eid in chunk]},
                )
                if r.status_code != 200:
                    continue
                for row in r.json().get("results", []):
                    targets = row.get("to") or []
                    if targets:
                        out[str(row.get("from", {}).get("id"))] = str(targets[0].get("toObjectId"))
            except Exception:
                logger.warning("메일-티켓 연결 조회 실패 (%d건)", len(chunk), exc_info=True)
        return out

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

    def search_contacts_changed_since(
        self, since: datetime, limit: int = 200
    ) -> list[ContactDTO]:
        """마지막 스윕 이후 바뀐 연락처. 폴러가 웹훅의 그물 밑을 받칩니다.

        티켓 검색과 같은 모양입니다 — ``hs_lastmodifieddate`` 로 자르고 오름차순으로 페이지를
        따라갑니다. 돌려주는 것은 같은 ``ContactDTO`` 라, 부르는 쪽이 웹훅으로 온 건과 스윕으로
        온 건을 다르게 다룰 일이 없습니다.
        """
        headers = {"Authorization": f"Bearer {self.token}"}
        ts_ms = str(int(since.timestamp() * 1000))
        contacts: list[ContactDTO] = []
        after: str | None = None
        with httpx.Client(headers=headers, timeout=30.0) as client:
            while len(contacts) < limit:
                body: dict = {
                    "filterGroups": [
                        {
                            "filters": [
                                {
                                    "propertyName": "lastmodifieddate",
                                    "operator": "GT",
                                    "value": ts_ms,
                                }
                            ]
                        }
                    ],
                    "sorts": [
                        {"propertyName": "lastmodifieddate", "direction": "ASCENDING"}
                    ],
                    "properties": _contact_properties().split(",") + ["lastmodifieddate"],
                    "limit": min(100, limit - len(contacts)),
                }
                if after:
                    body["after"] = after
                response = _sync_request_with_retries(
                    client, "POST", f"{BASE_URL}/crm/v3/objects/contacts/search", json=body
                )
                response.raise_for_status()
                page = response.json()
                for item in page.get("results", []):
                    props = item.get("properties") or {}
                    contacts.append(
                        ContactDTO(
                            id=str(item["id"]),
                            email=props.get("email"),
                            firstname=props.get("firstname"),
                            lastname=props.get("lastname"),
                            company=props.get("company"),
                            phone=props.get("phone"),
                            country=props.get("country"),
                            ip_country=props.get("ip_country") or props.get("hs_ip_country"),
                            lifecyclestage=props.get("lifecyclestage"),
                            updated_at=_parse_ts(props.get("lastmodifieddate")),
                            plan=props.get("plan"),
                            user_seq=props.get("user_seq"),
                            industry=props.get("industry"),
                        )
                    )
                after = page.get("paging", {}).get("next", {}).get("after")
                if not after:
                    break
        return contacts

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
