"""Self-healing Google Sheets sync for inquiries missed during an outage."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError

from .inbound_scoring import _normalize_email
from .stage_sync import _retire_superseded_drafts
from ..db.models import (
    Client,
    Contact,
    ContractRecord,
    Conversation,
    CustomerProfile,
    Event,
    Message,
)
from ..common.sheet_values import country_in_korean, normalise_plan
from ..db.session import SessionLocal
from ..integrations.google_sheets import (
    read_inbound_records,
    record_inbound,
    record_order,
    suggest_inbound_client_id,
    writes_enabled,
)

logger = logging.getLogger(__name__)

SHEET_SYNC_REQUESTED = "sheet_sync_requested"
_INBOUND_CLIENT_ID_LOCK = 0x504552534F434944  # "PERSOCID", Postgres advisory xact lock
SHEET_SYNC_STARTED = "sheet_sync_started"
SHEET_SYNC_COMPLETED = "sheet_sync_completed"
SHEET_SYNC_FAILED = "sheet_sync_failed"
SHEET_SYNC_TERMINAL_KINDS = (SHEET_SYNC_COMPLETED, SHEET_SYNC_FAILED)


def request_full_sheet_sync(actor: str) -> str:
    """Durably enqueue a full import/backfill without blocking the web request."""
    request_id = uuid4().hex
    with SessionLocal() as session:
        session.add(
            Event(
                kind=SHEET_SYNC_REQUESTED,
                payload={
                    "request_id": request_id,
                    "actor": actor,
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
        session.commit()
    return request_id


def _pending_full_sync_request() -> dict | None:
    """Return the oldest request without a terminal event."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(Event)
            .where(
                Event.kind.in_(
                    (SHEET_SYNC_REQUESTED, SHEET_SYNC_COMPLETED, SHEET_SYNC_FAILED)
                )
            )
            .order_by(Event.id)
        ).all()
    terminal_ids = {
        str(row.payload.get("request_id"))
        for row in rows
        if row.kind in SHEET_SYNC_TERMINAL_KINDS and row.payload
    }
    for row in rows:
        payload = row.payload or {}
        request_id = str(payload.get("request_id") or "")
        if row.kind == SHEET_SYNC_REQUESTED and request_id and request_id not in terminal_ids:
            return {**payload, "event_id": row.id}
    return None


def full_sheet_sync_status() -> dict | None:
    """Return the latest manual sync request and its durable status."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(Event)
            .where(
                Event.kind.in_(
                    (
                        SHEET_SYNC_REQUESTED,
                        SHEET_SYNC_STARTED,
                        SHEET_SYNC_COMPLETED,
                        SHEET_SYNC_FAILED,
                    )
                )
            )
            .order_by(Event.id.desc())
        ).all()
    if not rows:
        return None
    request = next((row for row in rows if row.kind == SHEET_SYNC_REQUESTED), None)
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
        "status": latest.kind.removeprefix("sheet_sync_"),
        "updated_at": latest.created_at,
    }


def process_requested_sheet_sync() -> bool:
    """Process one durable manual request; every write is idempotent on retry."""
    request = _pending_full_sync_request()
    if request is None:
        return False
    request_id = request["request_id"]
    with SessionLocal() as session:
        session.add(
            Event(
                kind=SHEET_SYNC_STARTED,
                payload={"request_id": request_id, "started_at": datetime.now(timezone.utc).isoformat()},
            )
        )
        session.commit()
    try:
        imported = import_inbound_history(5000)
        inbound = sync_pending_inbound_rows(200)
        orders = sync_pending_order_rows(200)
    except Exception as exc:
        logger.exception("Requested Google Sheets synchronization failed")
        with SessionLocal() as session:
            session.add(
                Event(
                    kind=SHEET_SYNC_FAILED,
                    payload={
                        "request_id": request_id,
                        "error": str(exc)[:500],
                        "failed_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            )
            session.commit()
        return False
    with SessionLocal() as session:
        session.add(
            Event(
                kind=SHEET_SYNC_COMPLETED,
                payload={
                    "request_id": request_id,
                    "imported": imported,
                    "inbound": inbound,
                    "orders": orders,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
        session.commit()
    return True


def _clean_sheet_value(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"n/a", "알 수 없음", "확인 안 됨"} else text


def _sheet_int(value: object) -> int | None:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _sheet_date(value: object) -> datetime | None:
    text = _clean_sheet_value(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for pattern in (
        "%Y. %m. %d.",
        "%Y. %m. %d",
        "%Y.%m.%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _stage_words(stage: str | None) -> tuple[str, str]:
    """로컬 단계 → 워크북의 (Deal Stage, Deal Stage Detail).

    표는 `google_sheets._STAGE_VALUES` 한 곳입니다 — 갱신 경로와 append 경로가 서로 다른
    말을 쓰면 같은 문의가 시트에서 두 얼굴을 갖습니다. 시트에 아직 말이 없는 단계는 New 로
    둡니다: 행이 아예 없는 것보다는 낫고, 갱신 경로가 그때 경고를 남깁니다.
    """
    from ..integrations.google_sheets import _STAGE_VALUES

    return _STAGE_VALUES.get(stage or "new", ("New", "Inquiry"))


def _local_stage(record: dict) -> str | None:
    """Workbook Deal Stage -> local stage. The inverse of google_sheets._STAGE_VALUES.

    Keep the two in step: this is the read half of a round trip, so a value that only
    one side knows means an import silently rewrites a stage the board just set.
    """
    stage = _clean_sheet_value(record.get("deal_stage")).lower().replace(" ", "_")
    return {
        "new": "new",
        "meeting_link_sent": "meeting_link_sent",
        "negotiation": "negotiation",
        "won": "won",
        "lost_rejected": "closed_lost",
        # 셋 다 `closed` 입니다. 쓰는 말은 Concluded 하나지만(2026-08-19), 시트에는 그
        # 전에 사람이 적어 둔 옛 이름이 남아 있습니다 — 허브스팟에서 No Response 와
        # Not a Fit 이 이 한 단계로 합쳐졌으므로 읽을 때도 같은 곳으로 접습니다. 안 그러면
        # 그 행들은 「모르는 값」이 되어 전체 동기화가 단계를 그냥 지나칩니다.
        "concluded": "closed",
        "not_a_fit": "closed",
        "no_response": "closed",
    }.get(stage)


def _profile_time_key(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _customer_state(stage: str) -> str:
    from .stage_sync import STATE_FOR_STAGE

    return STATE_FOR_STAGE.get(stage, "negotiation")


def _import_inbound_records(records: list[dict]) -> int:
    """Import a snapshot in one transaction; safe to retry after a unique race."""
    prepared: list[dict] = []
    for record in records:
        client_id = _sheet_int(record.get("client_id"))
        if not client_id:
            continue
        raw_email = _clean_sheet_value(record.get("email")).lower()
        email = raw_email if "@" in raw_email else ""
        inquiry_key = _clean_sheet_value(record.get("inquiry_key"))
        if not inquiry_key:
            # Legacy rows predate the Inquiry ID column. Use business content rather
            # than physical row position so sorting the workbook does not duplicate
            # the local conversation on the next import.
            fingerprint = "\x1f".join(
                _clean_sheet_value(record.get(key))
                for key in ("client_id", "inquiry_date", "email", "history")
            )
            inquiry_key = f"legacy:{client_id}:{hashlib.sha256(fingerprint.encode()).hexdigest()[:20]}"
        prepared.append(
            {
                "record": record,
                "client_id": client_id,
                "inquiry_key": inquiry_key,
                "email": email,
                "normalized_email": _normalize_email(email) if email else "",
                "inquiry_at": _sheet_date(record.get("inquiry_date")),
                "stage": _local_stage(record),
            }
        )
    if not prepared:
        return 0

    client_ids = {item["client_id"] for item in prepared}
    normalized_emails = {
        item["normalized_email"] for item in prepared if item["normalized_email"]
    }
    with SessionLocal() as session:
        contact_filters = [Contact.sheet_client_id.in_(client_ids)]
        if normalized_emails:
            contact_filters.append(Contact.normalized_email.in_(normalized_emails))
        contacts = session.scalars(
            select(Contact).where(or_(*contact_filters)).order_by(Contact.id)
        ).all()
        contacts_by_client = {
            contact.sheet_client_id: contact
            for contact in contacts
            if contact.sheet_client_id is not None
        }
        contacts_by_email = {contact.normalized_email: contact for contact in contacts}

        for item in prepared:
            client_id = item["client_id"]
            normalized_email = item["normalized_email"]
            sheet_contact = contacts_by_client.get(client_id)
            email_contact = contacts_by_email.get(normalized_email) if normalized_email else None
            # A real email identity wins over an old ``sheet:<id>`` placeholder.
            contact = email_contact or sheet_contact
            record = item["record"]
            if contact is None:
                contact = Contact(
                    normalized_email=normalized_email or f"sheet:{client_id}",
                    email=item["email"] or None,
                    full_name=_clean_sheet_value(record.get("full_name")) or "이름 미확인",
                    company=_clean_sheet_value(record.get("company")) or None,
                    phone=_clean_sheet_value(record.get("phone")) or None,
                    country=_clean_sheet_value(record.get("country")) or None,
                    sheet_client_id=client_id,
                )
                session.add(contact)
            else:
                if (
                    normalized_email
                    and contact.normalized_email.startswith(("sheet:", "unknown:"))
                    and email_contact is None
                ):
                    contacts_by_email.pop(contact.normalized_email, None)
                    contact.normalized_email = normalized_email
                    contacts_by_email[normalized_email] = contact
                contact.sheet_client_id = contact.sheet_client_id or client_id
                contact.email = contact.email or item["email"] or None
                contact.company = (
                    contact.company or _clean_sheet_value(record.get("company")) or None
                )
                contact.phone = contact.phone or _clean_sheet_value(record.get("phone")) or None
                contact.country = (
                    contact.country or _clean_sheet_value(record.get("country")) or None
                )
            contacts_by_client.setdefault(client_id, contact)
            if normalized_email:
                contacts_by_email.setdefault(normalized_email, contact)
            item["contact"] = contact

        # Allocate all new contact IDs with one flush, not one transaction per row.
        session.flush()

        inquiry_keys = {item["inquiry_key"] for item in prepared}
        conversations = session.scalars(
            select(Conversation).where(
                or_(
                    Conversation.sheet_inquiry_key.in_(inquiry_keys),
                    Conversation.sheet_client_id.in_(client_ids),
                )
            )
        ).all()
        conversations_by_key = {
            conversation.sheet_inquiry_key: conversation
            for conversation in conversations
            if conversation.sheet_inquiry_key
        }
        conversations_per_client: dict[int, list[Conversation]] = {}
        for conversation in conversations:
            if conversation.sheet_client_id is not None:
                conversations_per_client.setdefault(conversation.sheet_client_id, []).append(
                    conversation
                )
        fallback_claimed: set[int] = set()
        fallback_now = datetime.now(timezone.utc)
        for item in prepared:
            client_id = item["client_id"]
            contact = item["contact"]
            record = item["record"]
            inquiry_at = item["inquiry_at"] or fallback_now
            stage = item["stage"]
            inquiry_key = item["inquiry_key"]
            conversation = conversations_by_key.get(inquiry_key)
            if conversation is None:
                # One pre-0085 row can be adopted safely. Once a Client ID has more
                # than one conversation, only Inquiry ID may choose between them.
                legacy_candidates = [
                    candidate
                    for candidate in conversations_per_client.get(client_id, [])
                    if candidate.id not in fallback_claimed
                    and candidate.sheet_inquiry_key not in inquiry_keys
                ]
                if len(legacy_candidates) == 1:
                    conversation = legacy_candidates[0]
                    fallback_claimed.add(conversation.id)
                    conversations_by_key.pop(conversation.sheet_inquiry_key, None)
                    conversation.sheet_inquiry_key = inquiry_key
                    conversations_by_key[inquiry_key] = conversation
            if conversation is None:
                conversation = Conversation(
                    contact_id=contact.id,
                    stage=stage or "new",
                    last_incoming_at=inquiry_at,
                    sheet_inbound_row=_sheet_int(record.get("_row")),
                    sheet_client_id=client_id,
                    sheet_inquiry_key=inquiry_key,
                )
                session.add(conversation)
                conversations_by_key[inquiry_key] = conversation
                conversations_per_client.setdefault(client_id, []).append(conversation)
            else:
                # Reattach a legacy placeholder conversation to the canonical
                # email contact, but never downgrade a known stage on schema drift.
                conversation.contact_id = contact.id
                # **티켓이 있는 문의는 허브스팟이 기준입니다.** 워크북 셀은 사람이 손으로
                # 고치는 값이라 늦기 쉬운데, 예전에는 시간 비교 없이 마지막에 쓴 쪽이
                # 이겼습니다 — 허브스팟에서 옮긴 단계가 다음 전체 동기화에서 시트의 옛
                # 값으로 되돌아갔습니다. 시트가 원본인 것은 티켓 없는 행(워크북에서만 사는
                # 문의)뿐입니다.
                if stage is not None and conversation.hubspot_ticket_id:
                    if conversation.stage != stage:
                        logger.info(
                            "워크북의 '%s' 는 무시합니다 — 티켓 %s 의 단계는 HubSpot 이 정합니다 (지금 '%s')",
                            stage, conversation.hubspot_ticket_id, conversation.stage,
                        )
                    stage = None
                if stage is not None:
                    # 워크북에서 읽어 온 단계도 단계입니다. **바뀔 때만** 닫는 이유는
                    # hubspot_backfill 과 같습니다: 이 루프는 행마다 도는데 여러 행이 한
                    # Client ID 를 가리킬 수 있고, SessionLocal 이 autoflush=False 라
                    # 두 번째 조회도 아직 pending_approval 인 그 행을 그대로 찾아 옵니다
                    # — 종료 기록만 문의 하나에 여러 줄이 됩니다. id 가 없는 것은 방금
                    # 이 루프가 만든 행이라 초안이 있을 수 없습니다.
                    if conversation.id and conversation.stage != stage:
                        _retire_superseded_drafts(session, conversation.id, stage)
                    conversation.stage = stage
                conversation.sheet_inbound_row = conversation.sheet_inbound_row or _sheet_int(
                    record.get("_row")
                )
                conversation.last_incoming_at = conversation.last_incoming_at or inquiry_at
            item["conversation"] = conversation

        session.flush()
        conversation_ids = {
            item["conversation"].id for item in prepared if item.get("conversation") is not None
        }
        conversations_with_inbound = set(
            session.scalars(
                select(Message.conversation_id)
                .where(
                    Message.conversation_id.in_(conversation_ids),
                    Message.direction == "inbound",
                )
                .distinct()
            ).all()
        )
        for item in prepared:
            conversation = item["conversation"]
            record = item["record"]
            history = _clean_sheet_value(record.get("history"))
            if conversation.id not in conversations_with_inbound and history:
                session.add(
                    Message(
                        conversation_id=conversation.id,
                        direction="inbound",
                        channel=_clean_sheet_value(record.get("channel")) or "sheet_import",
                        from_address=item["email"] or None,
                        body=history,
                        status="received",
                        created_at=item["inquiry_at"] or fallback_now,
                    )
                )
                conversations_with_inbound.add(conversation.id)

        # A contact may have several inquiry rows. Profile fields represent the
        # newest inquiry, not whichever row happens to be physically last today.
        latest_by_contact: dict[int, dict] = {}
        for item in prepared:
            contact_id = item["contact"].id
            conversation = item["conversation"]
            profile_at = item["inquiry_at"] or conversation.last_incoming_at
            row_number = _sheet_int(item["record"].get("_row")) or 0
            current = latest_by_contact.get(contact_id)
            current_key = (
                _profile_time_key(current["profile_at"]),
                current["row_number"],
            ) if current else None
            candidate_key = (_profile_time_key(profile_at), row_number)
            if current_key is None or candidate_key > current_key:
                item["profile_at"] = profile_at
                item["row_number"] = row_number
                latest_by_contact[contact_id] = item

        contact_ids = set(latest_by_contact)
        profiles = {
            profile.contact_id: profile
            for profile in session.scalars(
                select(CustomerProfile).where(CustomerProfile.contact_id.in_(contact_ids))
            ).all()
        }
        for contact_id, item in latest_by_contact.items():
            profile = profiles.get(contact_id) or CustomerProfile(contact_id=contact_id)
            stage = item["stage"]
            if stage is not None:
                profile.pipeline_stage = stage
                profile.customer_state = _customer_state(stage)
            record = item["record"]
            profile.qualification = (
                _clean_sheet_value(record.get("pipeline")) or profile.qualification
            )
            profile.industry = _clean_sheet_value(record.get("company_type")) or profile.industry
            profile.current_plan = _clean_sheet_value(record.get("plan")) or profile.current_plan
            profile.user_seq = _clean_sheet_value(record.get("user_seq")) or profile.user_seq
            profile.source = _clean_sheet_value(record.get("source")) or profile.source
            session.add(profile)

        session.commit()
    return len(prepared)


def import_inbound_history(limit: int = 5000) -> int:
    """Idempotently pull the existing Inbound DB into the local customer history."""
    records = read_inbound_records(limit=limit)
    for attempt in range(2):
        try:
            return _import_inbound_records(records)
        except IntegrityError:
            if attempt:
                raise
            logger.info("Concurrent sheet import detected; retrying against committed rows.")
    return 0


def pending_inbound_count() -> int:
    with SessionLocal() as session:
        return len(
            session.execute(
                select(Conversation.id)
                .where(
                    Conversation.sheet_inbound_row.is_(None),
                    Conversation.last_incoming_at.isnot(None),
                )
            ).all()
        )


def pending_order_count() -> int:
    with SessionLocal() as session:
        return len(
            session.execute(
                select(ContractRecord.id).where(
                    ContractRecord.status.in_(("contracted", "active")),
                    ContractRecord.sheet_synced_at.is_(None),
                )
            ).all()
        )


def reserve_inbound_client_id(conversation_id: int) -> int | None:
    """Reserve the company's Client ID and this inquiry's separate row key."""
    if not writes_enabled():
        return None
    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            return None
        contact = session.get(Contact, conversation.contact_id)
        if not conversation.sheet_inquiry_key:
            conversation.sheet_inquiry_key = (
                f"hubspot:{conversation.hubspot_ticket_id}"
                if conversation.hubspot_ticket_id
                else f"conversation:{conversation.id}"
            )
        from .client_ids import find_existing_client_id

        existing = find_existing_client_id(session, contact)
        if existing or conversation.sheet_client_id:
            client_id = existing or conversation.sheet_client_id
            conversation.sheet_client_id = client_id
            if contact and not contact.sheet_client_id:
                contact.sheet_client_id = client_id
            session.commit()
            return client_id

    suggested = suggest_inbound_client_id()
    for _attempt in range(5):
        with SessionLocal() as session:
            bind = session.get_bind()
            if bind.dialect.name == "postgresql":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": _INBOUND_CLIENT_ID_LOCK},
                )
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                return None
            contact = session.get(Contact, conversation.contact_id)
            if not conversation.sheet_inquiry_key:
                conversation.sheet_inquiry_key = (
                    f"hubspot:{conversation.hubspot_ticket_id}"
                    if conversation.hubspot_ticket_id
                    else f"conversation:{conversation.id}"
                )

            from .client_ids import find_existing_client_id, next_client_id

            existing = find_existing_client_id(session, contact)
            if existing:
                conversation.sheet_client_id = existing
                if contact and not contact.sheet_client_id:
                    contact.sheet_client_id = existing
                session.commit()
                return existing

            local_next = next_client_id(session, "GTM Inbound")
            candidate = max(suggested, local_next)
            if candidate >= 2000:
                raise RuntimeError("Inbound Client ID 1000-series is exhausted.")
            conversation.sheet_client_id = candidate
            if contact:
                contact.sheet_client_id = candidate
            try:
                session.commit()
                return candidate
            except IntegrityError:
                # A concurrent reservation can still win the inquiry-key insert.
                # Reload before trying again; production currently has one worker,
                # while this keeps webhook/poller overlap idempotent.
                session.rollback()
                suggested = candidate + 1
    raise RuntimeError("Could not reserve an Inbound Client ID.")


def _order_record(contact: Contact, contract: ContractRecord) -> dict:
    fields = dict(contract.sheet_fields or {})
    order_when = contract.contract_date or contract.created_at or datetime.now(timezone.utc)
    amount = contract.amount
    instrument = fields.get("payment_instrument") or {
        "stripe": "Stripe",
        "portone": "포트원",
        "bank_transfer": "계좌이체",
    }.get(contract.payment_method or "", contract.payment_method or "")
    return {
        "client_id": contract.sheet_client_id,
        "department": "GTM",
        "customer_classification": "Inbound",
        "order_date": order_when.date().isoformat(),
        "company": contact.company,
        "contract_method": fields.get("contract_method", ""),
        "payment_instrument": instrument,
        "first_payment_date": contract.paid_at.date().isoformat() if contract.paid_at else "",
        "account_status": "사용 중" if contract.status == "active" else "도입 준비",
        "payment_method": fields.get("payment_terms", "일시불"),
        "billing_email": fields.get("billing_email") or contact.email,
        "contract_months": fields.get("contract_months", ""),
        # Google Sheets stores numbers as doubles; keep the database Decimal as
        # the source of truth and only convert at this integration boundary.
        "amount": (
            int(amount) if amount is not None and amount == amount.to_integral_value()
            else float(amount) if amount is not None
            else None
        ),
        "currency": contract.currency,
        "notes": contract.notes or "",
        "owner_email": fields.get("owner_email", ""),
        "space_seq": fields.get("space_seq", ""),
        "plan_start_date": fields.get("plan_start_date", ""),
        "enterprise_name": fields.get("enterprise_name") or contact.company,
        "invitation_limit": fields.get("invitation_limit", ""),
        "queue_limit": fields.get("queue_limit", ""),
        "concurrent_jobs": fields.get("concurrent_jobs", ""),
        "space_count": fields.get("space_count", ""),
        "credits": fields.get("contract_credits", ""),
        "credit_history": fields.get("credit_history", ""),
        "payer": fields.get("payer", ""),
        "plan_notes": fields.get("plan_notes", ""),
        "payment_month": order_when.strftime("%Y-%m"),
        "payment_quarter": f"{order_when.year}-Q{(order_when.month - 1) // 3 + 1}",
    }


def sync_contract_order(contract_id: int) -> bool:
    if not writes_enabled():
        return False
    with SessionLocal() as session:
        contract = session.get(ContractRecord, contract_id)
        if not contract or contract.status not in {"contracted", "active"}:
            return False
        contact = session.get(Contact, contract.contact_id)
        if not contact:
            return False
        if not contract.sheet_client_id and contract.conversation_id:
            conversation = session.get(Conversation, contract.conversation_id)
            if conversation and conversation.sheet_client_id:
                contract.sheet_client_id = conversation.sheet_client_id
                session.commit()
        if not contract.sheet_client_id:
            return False
        record = _order_record(contact, contract)

    result = record_order(record)
    if not result or not result.row:
        return False
    with SessionLocal() as session:
        contract = session.get(ContractRecord, contract_id)
        if not contract:
            return False
        contract.sheet_order_row = result.row
        contract.sheet_synced_at = datetime.now(timezone.utc)
        session.commit()
    return True


def sync_pending_order_rows(limit: int = 50) -> int:
    with SessionLocal() as session:
        ids = session.scalars(
            select(ContractRecord.id)
            .where(
                ContractRecord.status.in_(("contracted", "active")),
                ContractRecord.sheet_synced_at.is_(None),
            )
            .order_by(ContractRecord.created_at)
            .limit(limit)
        ).all()
    return sum(sync_contract_order(contract_id) for contract_id in ids)


def sync_pending_inbound_rows(limit: int = 50) -> int:
    """Backfill unsynced conversations; safe to run every poller tick."""
    if not writes_enabled():
        return 0
    with SessionLocal() as session:
        ids = session.scalars(
            select(Conversation.id)
            .where(
                Conversation.sheet_inbound_row.is_(None),
                Conversation.last_incoming_at.isnot(None),
            )
            .order_by(Conversation.created_at)
            .limit(limit)
        ).all()

    synced = 0
    for conversation_id in ids:
        try:
            reserved_client_id = reserve_inbound_client_id(conversation_id)
        except Exception:
            logger.warning(
                "Could not reserve Inbound Client ID for conversation %d.",
                conversation_id,
                exc_info=True,
            )
            continue
        with SessionLocal() as session:
            conv = session.get(Conversation, conversation_id)
            if not conv or conv.sheet_inbound_row:
                continue
            contact = session.get(Contact, conv.contact_id)
            profile = session.get(CustomerProfile, conv.contact_id)
            inbound = session.scalars(
                select(Message)
                .where(Message.conversation_id == conv.id, Message.direction == "inbound")
                .order_by(Message.created_at)
                .limit(1)
            ).first()
            if not contact or not inbound:
                continue
            when = inbound.created_at
            qualification = (profile.qualification if profile else None) or "MQL"
            # 고객사 이름·산업·국가는 「고객 기본 정보」가 원본입니다. 그 Client ID 가 이미
            # 수주 고객이면 거기 적힌 철자를 씁니다 — 같은 회사가 세 번 문의해도 서울대학교 /
            # 서울대 / SNU 로 갈라지지 않도록. 시트 수식으로는 이걸 못 합니다: Inbound DB 가
            # 고객 기본 정보를 조회하면 그 반대 방향과 순환 참조가 되고, 조회가 빈 리드
            # 153건은 이름을 잃습니다. 값으로 쓰면 둘 다 없습니다.
            master = session.get(Client, reserved_client_id) if reserved_client_id else None
            legacy_inquiry_keys = session.scalars(
                select(Conversation.sheet_inquiry_key).where(
                    Conversation.id != conv.id,
                    Conversation.sheet_client_id == reserved_client_id,
                    Conversation.sheet_inbound_row.isnot(None),
                    Conversation.sheet_inquiry_key.isnot(None),
                )
            ).all()
            record = {
                "client_id": reserved_client_id,
                "inquiry_key": conv.sheet_inquiry_key,
                "_legacy_inquiry_keys": legacy_inquiry_keys,
                "sales_direction": "Inbound",
                "inquiry_date": when.date().isoformat(),
                # 단계는 그 문의의 **지금** 값입니다. 예전에는 "New" 로 박혀 있어서, 몇
                # 주째 협상 중인 문의도 워크북에는 New 로 들어갔습니다 — 그리고 전체
                # 동기화가 그 값을 우리 DB 로 되돌려 단계를 잃었습니다. 시트가 표현 못 하는
                # 단계는 New 로 두되(행 자체는 있어야 합니다) 어긋난다고 남깁니다.
                "deal_stage": _stage_words(conv.stage)[0],
                "deal_stage_detail": _stage_words(conv.stage)[1],
                "pipeline": qualification,
                "company": (master.company if master else None) or contact.company or "알 수 없음",
                "full_name": contact.full_name,
                "phone": contact.phone or "알 수 없음",
                "email": contact.email or "",
                "country": (master.country if master else None)
                or country_in_korean(contact.country),
                "company_type": (master.industry if master else None)
                or (profile.industry if profile else None)
                or "확인 안 됨",
                "channel": "허브스팟" if contact.hubspot_contact_id else inbound.channel,
                "plan": normalise_plan(profile.current_plan if profile else None),
                "user_seq": profile.user_seq if profile else "",
                "source": profile.source if profile else "",
                "history": inbound.body.replace("\n", " ")[:2000],
                "inquiry_month": when.strftime("%Y-%m"),
                "inquiry_quarter": f"{when.year}-Q{(when.month - 1) // 3 + 1}",
            }

        result = record_inbound(record)
        if not result or not result.row:
            continue
        with SessionLocal() as session:
            conv = session.get(Conversation, conversation_id)
            if not conv or conv.sheet_inbound_row:
                continue
            contact = session.get(Contact, conv.contact_id)
            conv.sheet_inbound_row = result.row
            conv.sheet_client_id = result.client_id
            if contact and result.client_id and not contact.sheet_client_id:
                contact.sheet_client_id = result.client_id
            session.commit()
        synced += 1
    if synced:
        logger.info("Backfilled %d inbound Google Sheet row(s).", synced)
    return synced
