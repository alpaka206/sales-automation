"""One-shot backfill of the [B2B] AI Dubbing ticket pipeline into our own tables.

The inbound pipeline only ever ingests tickets that arrive in the New stage, so a
portal with years of history shows up here as a single card. This walks every
ticket in the pipeline — all stages — and creates the Contact/Conversation rows the
console renders, so the board reflects reality from day one.

Safety, deliberately:

- **It cannot send mail or draft a reply.** It never calls ``InboundAgent.handle``
  and never enqueues an ``InboundJob``; the inbound worker claims work only from
  ``inbound_jobs`` and the send worker only from ``messages`` with
  ``status='approved'``, so rows created here are invisible to both. No ORM event
  or DB trigger exists that could bridge that gap.
- **It writes nothing to HubSpot.** Reads only — unaffected by the pre-launch guard.
- **It leaves ``last_incoming_at`` NULL.** ``sheet_sync.sync_pending_inbound_rows``
  selects exactly ``sheet_inbound_row IS NULL AND last_incoming_at IS NOT NULL``
  and runs every poller tick; setting it would queue all 300+ rows to be appended
  to the shared sales workbook the moment ``LIVE_EXTERNAL_WRITES`` is turned on,
  and would inflate the pipeline page's "미처리" badge even before that.
- **It creates no Message rows**, so it does not suppress the auto-ack on a thread
  that later receives a genuine inquiry (``is_first_inbound`` counts inbound
  messages, not conversations).

Re-running is safe: contacts key on ``normalized_email`` and conversations on
``hubspot_ticket_id``, both uniquely indexed, and both are upserted.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from ..common.config import settings
from ..common.domains import is_personal_domain
from ..db.models import Contact, Conversation, CustomerProfile, Event
from ..db.session import SessionLocal
from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured
from .stage_sync import _retire_superseded_drafts, local_stage_for

logger = logging.getLogger(__name__)

# Event-driven state machine, mirroring sheet_sync. The status shown in the UI is
# the kind with this prefix stripped, so every kind must read "<prefix>_<status>".
BACKFILL_REQUESTED = "hubspot_backfill_requested"
BACKFILL_STARTED = "hubspot_backfill_started"
BACKFILL_COMPLETED = "hubspot_backfill_completed"
BACKFILL_FAILED = "hubspot_backfill_failed"
BACKFILL_TERMINAL_KINDS = (BACKFILL_COMPLETED, BACKFILL_FAILED)

# The [B2B] AI Dubbing ticket pipeline.
B2B_PIPELINE_ID = "798618015"


def request_hubspot_backfill(actor: str) -> str:
    """Durably enqueue the backfill; the poller runs it on its next tick."""
    request_id = uuid4().hex
    with SessionLocal() as session:
        session.add(
            Event(
                kind=BACKFILL_REQUESTED,
                payload={
                    "request_id": request_id,
                    "actor": actor,
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
        session.commit()
    return request_id


def _pending_request() -> dict | None:
    """The oldest request with no terminal event. STARTED is not terminal, so a
    crash mid-run leaves the request pending and the next tick retries it."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(Event)
            .where(Event.kind.in_((BACKFILL_REQUESTED, *BACKFILL_TERMINAL_KINDS)))
            .order_by(Event.id)
        ).all()
    terminal = {
        str(row.payload.get("request_id"))
        for row in rows
        if row.kind in BACKFILL_TERMINAL_KINDS and row.payload
    }
    for row in rows:
        payload = row.payload or {}
        request_id = str(payload.get("request_id") or "")
        if row.kind == BACKFILL_REQUESTED and request_id and request_id not in terminal:
            return {**payload, "event_id": row.id}
    return None


def hubspot_backfill_status() -> dict | None:
    """Latest backfill request and its durable status, for the pipeline page."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(Event)
            .where(
                Event.kind.in_(
                    (BACKFILL_REQUESTED, BACKFILL_STARTED, *BACKFILL_TERMINAL_KINDS)
                )
            )
            .order_by(Event.id.desc())
        ).all()
    if not rows:
        return None
    request = next((row for row in rows if row.kind == BACKFILL_REQUESTED), None)
    if request is None or not request.payload:
        return None
    request_id = str(request.payload.get("request_id") or "")
    latest = next(
        (
            row
            for row in rows
            if row.payload and str(row.payload.get("request_id") or "") == request_id
        ),
        request,
    )
    return {
        **request.payload,
        **(latest.payload or {}),
        "status": latest.kind.removeprefix("hubspot_backfill_"),
        "updated_at": latest.created_at,
    }


def _display_name(dto) -> str:
    """Contact.full_name is NOT NULL, so always produce something."""
    parts = [p for p in (dto.firstname, dto.lastname) if p and p.strip()]
    if parts:
        return " ".join(parts).strip()
    if dto.email:
        return dto.email.split("@", 1)[0]
    return "이름 미확인"


def backfill_b2b_pipeline(pipeline: str = B2B_PIPELINE_ID) -> dict:
    """Pull every ticket of one pipeline into contacts + conversations.

    Returns counts. Raises HubSpotNotConfigured when there is no token.
    """
    client = HubSpotClient()
    fetched = client.list_tickets_with_contacts_sync(pipeline=pipeline)
    # Paging over a live table can hand back the same ticket twice when rows shift
    # between pages; keep the first sighting so the loop never inserts a duplicate.
    seen_tickets: set[str] = set()
    pairs = []
    for ticket, ids in fetched:
        if ticket.id in seen_tickets:
            continue
        seen_tickets.add(ticket.id)
        pairs.append((ticket, ids))

    contact_ids = [cid for _ticket, ids in pairs for cid in ids]
    contacts_by_id = client.get_contacts_batch_sync(contact_ids)

    tickets = len(pairs)
    created_contacts = created_convs = updated_convs = skipped = 0

    with SessionLocal() as session:
        for ticket, ids in pairs:
            dto = next((contacts_by_id[i] for i in ids if i in contacts_by_id), None)
            if dto is None:
                # 연락처가 안 붙은 티켓(또는 허브스팟에서 지워진 연락처). 예전에는 여기서
                # 건너뛰었고, 그 대가가 화면 건수가 허브스팟보다 적은 것이었습니다 —
                # `_placeholder_contact` 의 설명을 보세요.
                contact = _placeholder_contact(session, ticket)
                skipped += 1
                email = ""
            else:
                email = (dto.email or "").strip().lower()
                normalized = email or f"unknown:hs-{dto.id}"
                contact = session.scalar(
                    select(Contact).where(Contact.normalized_email == normalized)
                )
            if dto is not None and contact is None:
                domain = email.split("@", 1)[1] if "@" in email else ""
                contact = Contact(
                    normalized_email=normalized,
                    email=email or None,
                    full_name=_display_name(dto),
                    company=dto.company or None,
                    phone=dto.phone or None,
                    country=dto.country or None,
                    # Personal mailboxes must never be grouped as one company.
                    domain=domain if domain and not is_personal_domain(domain) else None,
                    hubspot_contact_id=dto.id,
                )
                session.add(contact)
                session.flush()
                created_contacts += 1
            elif dto is not None:
                contact.email = contact.email or (email or None)
                contact.company = contact.company or dto.company or None
                contact.phone = contact.phone or dto.phone or None
                contact.country = contact.country or dto.country or None
                contact.hubspot_contact_id = contact.hubspot_contact_id or dto.id

            stage = local_stage_for(ticket.pipeline_stage) or "new"
            conv = session.scalar(
                select(Conversation).where(Conversation.hubspot_ticket_id == ticket.id)
            )
            if conv is None:
                conv = Conversation(
                    contact_id=contact.id,
                    hubspot_ticket_id=ticket.id,
                    stage=stage,
                    inquiry_subject=ticket.subject or None,
                    # last_incoming_at stays NULL on purpose — see module docstring.
                    created_at=ticket.created_at or datetime.now(timezone.utc),
                )
                session.add(conv)
                created_convs += 1
            elif conv.stage != stage:
                conv.stage = stage
                # 백필이 옮긴 단계도 단계입니다. 이 티켓은 최근에 바뀐 것이 아니라서
                # 10분 폴러의 stage reconcile(최근 변경분만 훑습니다)이 다시 오지
                # 않습니다 — 여기서 안 닫으면 그 초안은 계속 발송 대기에 남습니다.
                _retire_superseded_drafts(session, conv.id, stage)
                updated_convs += 1

            profile = session.get(CustomerProfile, contact.id)
            if profile is None:
                profile = CustomerProfile(contact_id=contact.id)
                session.add(profile)
                # SessionLocal runs with autoflush=False, so a pending row is
                # invisible to the next iteration's session.get(). One contact can
                # own several tickets (311 tickets, 272 contacts here), and without
                # this flush the second of them inserted a second profile with the
                # same primary key and the whole run died on commit.
                session.flush()
            profile.pipeline_stage = stage

        session.commit()

    result = {
        "tickets": tickets,
        "contacts_created": created_contacts,
        "conversations_created": created_convs,
        "conversations_updated": updated_convs,
        # 이제 건너뛰지 않고 자리 표시 연락처로 들여옵니다. 세는 것은 남깁니다 — 허브스팟에서
        # 연락처가 안 붙은 티켓이 몇 건인지는 알아 둘 값입니다.
        "no_contact_rows": skipped,
    }
    logger.info("HubSpot backfill finished: %s", result)
    return result


def _placeholder_contact(session, ticket):
    """연락처가 안 붙은 티켓의 자리 표시 연락처. 없으면 만들고, 있으면 그대로 씁니다.

    예전에는 이런 티켓을 통째로 건너뛰었습니다("붙일 사람이 없으니 지어내지 않는다"). 그
    판단의 대가가 **화면 건수가 허브스팟보다 적은 것**이었고, 운영자가 세어 보고 알아챘습니다
    (2026-08-18: Lost 3건 · Not a Fit 1건이 전부 이 경우였습니다). 파이프라인 건수는 맞아야
    합니다 — 안 맞으면 그 화면의 숫자를 아무도 못 믿습니다.

    키는 **티켓** 번호입니다(연락처 번호가 아니라). 그래야 같은 티켓을 다시 훑어도 한 행이고,
    연락처 없는 티켓 둘이 한 사람으로 합쳐지지 않습니다.

    메일이 갈 길은 없습니다: `email` 이 None 이고 메시지도 초안도 안 만듭니다.
    """
    normalized = f"unknown:ticket-{ticket.id}"
    contact = session.scalar(select(Contact).where(Contact.normalized_email == normalized))
    if contact is None:
        contact = Contact(
            normalized_email=normalized,
            email=None,
            full_name=(ticket.subject or "").strip() or "연락처 없는 티켓",
        )
        session.add(contact)
        session.flush()
    return contact


def adopt_ticket(ticket) -> bool:
    """우리가 아직 모르는 티켓 하나를 주워 옵니다. 새로 만들었으면 True.

    **왜 필요한가.** 접수 경로는 New 단계에 도착한 티켓만 들여옵니다
    (`inbound_poller.poll_tickets_once` 가 New 만 검색하고, 웹훅도 New 로의 이동만
    접수로 칩니다). 그래서 영업이 다른 파이프라인에서 끌어오거나 처음부터 Negotiating ·
    Lost · Not a Fit 으로 만든 티켓은 **우리 쪽에 행 자체가 안 생깁니다.** 단계 동기화는
    그때 고칠 대상이 없어서 조용히 지나갔고, 화면의 건수가 허브스팟보다 적었습니다.

    안전은 백필과 같습니다(그 모듈 docstring 참고): 메일도 초안도 만들지 않고, 접수 큐에
    넣지 않으며, ``last_incoming_at`` 을 NULL 로 둡니다 — 그 값이 차면 워크북 append 대기에
    올라갑니다. 여기서 만드는 것은 **보이기 위한 행**이지 처리할 일감이 아닙니다.

    부르는 쪽이 파이프라인을 먼저 거릅니다. 이 함수는 티켓 하나만 봅니다.
    """
    client = HubSpotClient()
    with SessionLocal() as session:
        if session.scalar(
            select(Conversation.id).where(Conversation.hubspot_ticket_id == str(ticket.id))
        ):
            return False

    contact_id = ticket.primary_contact_id or client.get_ticket_primary_contact_sync(str(ticket.id))
    dto = client.get_contact_sync(str(contact_id)) if contact_id else None

    stage = local_stage_for(ticket.pipeline_stage) or "new"
    with SessionLocal() as session:
        if dto is None:
            # 연락처가 안 붙은 티켓도 파이프라인의 한 건입니다 — `_placeholder_contact` 참고.
            contact = _placeholder_contact(session, ticket)
        else:
            email = (dto.email or "").strip().lower()
            normalized = email or f"unknown:hs-{dto.id}"
            contact = session.scalar(
                select(Contact).where(Contact.normalized_email == normalized)
            )
            if contact is None:
                domain = email.split("@", 1)[1] if "@" in email else ""
                contact = Contact(
                    normalized_email=normalized,
                    email=email or None,
                    full_name=_display_name(dto),
                    company=dto.company or None,
                    phone=dto.phone or None,
                    country=dto.country or None,
                    # Personal mailboxes must never be grouped as one company.
                    domain=domain if domain and not is_personal_domain(domain) else None,
                    hubspot_contact_id=dto.id,
                )
                session.add(contact)
                session.flush()
            else:
                contact.hubspot_contact_id = contact.hubspot_contact_id or dto.id

        session.add(
            Conversation(
                contact_id=contact.id,
                hubspot_ticket_id=str(ticket.id),
                stage=stage,
                inquiry_subject=ticket.subject or None,
                # last_incoming_at stays NULL on purpose — see the module docstring.
                created_at=ticket.created_at or datetime.now(timezone.utc),
            )
        )
        profile = session.get(CustomerProfile, contact.id)
        if profile is None:
            profile = CustomerProfile(contact_id=contact.id)
            session.add(profile)
            session.flush()
        profile.pipeline_stage = stage
        session.commit()

    logger.info("Adopted HubSpot ticket %s in stage %s", ticket.id, stage)
    return True


def process_requested_hubspot_backfill() -> bool:
    """Run one pending backfill request. Called from the poller tick."""
    request = _pending_request()
    if request is None:
        return False
    request_id = request["request_id"]

    with SessionLocal() as session:
        session.add(
            Event(
                kind=BACKFILL_STARTED,
                payload={
                    "request_id": request_id,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
        session.commit()

    try:
        counts = backfill_b2b_pipeline()
    except HubSpotNotConfigured as exc:
        _fail(request_id, f"HubSpot 토큰이 설정되지 않았습니다: {exc}")
        return False
    except Exception as exc:
        logger.exception("HubSpot backfill failed")
        _fail(request_id, str(exc))
        return False

    with SessionLocal() as session:
        session.add(
            Event(
                kind=BACKFILL_COMPLETED,
                payload={
                    "request_id": request_id,
                    **counts,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
        session.commit()
    return True


def _fail(request_id: str, error: str) -> None:
    with SessionLocal() as session:
        session.add(
            Event(
                kind=BACKFILL_FAILED,
                payload={
                    "request_id": request_id,
                    "error": error[:500],
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
        session.commit()


__all__ = [
    "B2B_PIPELINE_ID",
    "backfill_b2b_pipeline",
    "hubspot_backfill_status",
    "process_requested_hubspot_backfill",
    "request_hubspot_backfill",
    "settings",
]
