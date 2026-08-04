"""Reflect HubSpot-side ticket stage changes back into our local pipeline.

Sales moves tickets in HubSpot directly — a deal that gets answered on WhatsApp is
dragged to Negotiating there, not here. Until now none of that was visible: the
webhook accepted an ``hs_pipeline_stage`` change only when the NEW value equalled
``HUBSPOT_TICKET_STAGE_NEW`` and dropped every other transition, and the poller
searched only the New stage. So a ticket could go New → Negotiating → Won in HubSpot
while our board still showed it sitting in New.

This module closes that gap. It writes **only to our own database** (Conversation +
CustomerProfile), so it is unaffected by the pre-launch guard: safe mode blocks writes
*to* HubSpot, and reads *from* HubSpot stay on. Nothing here sends mail.

Direction matters: this is HubSpot → us. The reverse (our board moving a HubSpot
ticket) is ``customer_ops._sync_stage``, which does go through ``guard_external_write``.
"""

from __future__ import annotations

import logging

from ..db.conversation_history import add_progress
from ..db.models import Contact, Conversation, CustomerProfile
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

# Local stage key -> the Settings attribute holding its HubSpot stage id.
# Ordered like the pipeline. Kept here (not in customer_ops) because both the web
# routes and the background agents need it, and importing a routes module from an
# agent would be a circular import.
LOCAL_STAGE_TO_SETTING: dict[str, str] = {
    "new": "HUBSPOT_TICKET_STAGE_NEW",
    "meeting_link_sent": "HUBSPOT_TICKET_STAGE_AFTER_SEND",
    "negotiation": "HUBSPOT_TICKET_STAGE_NEGOTIATION",
    "reminder_sent": "HUBSPOT_TICKET_STAGE_REMINDER_SENT",
    "won": "HUBSPOT_TICKET_STAGE_WON",
    "closed_lost": "HUBSPOT_TICKET_STAGE_CLOSED_LOST",
    "closed": "HUBSPOT_TICKET_STAGE_CLOSED",
}

# Local stages that imply the customer relationship has moved on. THE one copy of this
# rule: customer_ops (operator move) and sheet_sync (workbook import) import it too, so
# all three paths leave the profile in the same shape. Before migration 0040 each kept
# its own divergent copy.
STATE_FOR_STAGE: dict[str, str] = {
    "won": "service",
    "closed_lost": "lost",
    "closed": "lost",
}


def customer_state_for(stage: str, current: str | None) -> str:
    """The customer_state a stage move implies, given the profile's current state."""
    settled = STATE_FOR_STAGE.get(stage)
    if settled:
        return settled
    if current in {"service", "lost"}:
        # Moved back into an open stage — reopen instead of leaving it closed.
        return "negotiation"
    return current or "negotiation"


def stage_id_to_local() -> dict[str, str]:
    """HubSpot stage id -> local stage key.

    Rebuilt per call from settings so a config change lands without a restart, and
    blank ids are skipped (otherwise every unconfigured stage would collide on "").
    """
    from ..common.config import settings

    mapping: dict[str, str] = {}
    for local, attr in LOCAL_STAGE_TO_SETTING.items():
        stage_id = (getattr(settings, attr, "") or "").strip()
        if stage_id:
            mapping.setdefault(stage_id, local)
    return mapping


def local_stage_for(hubspot_stage_id: str | None) -> str | None:
    """The local stage key for a HubSpot stage id, or None when unmapped."""
    if not hubspot_stage_id:
        return None
    return stage_id_to_local().get(str(hubspot_stage_id).strip())


def _mirror_stage_to_sheet(client_id: int | None, stage: str, ticket_id: str) -> None:
    """Push a HubSpot-driven stage move into the sales workbook, best effort.

    This is what makes the Sheet track HubSpot without anyone re-typing it. Three
    things gate it, all deliberate:

    - ``update_inbound_stage`` no-ops unless ``writes_enabled()``, i.e. until
      ``LIVE_EXTERNAL_WRITES`` is turned on. Pre-launch nothing is written.
    - It needs the workbook's stable Client ID. Rows created by
      ``hubspot_backfill`` have none, so the one-shot import can never write 300+
      rows into the shared sheet — only threads the Sheet already knows about are
      updated.
    - It never raises. A Sheets outage must not break the webhook (which would make
      HubSpot redeliver) or abort the poller sweep.
    """
    if not client_id:
        return
    try:
        from ..integrations.google_sheets import update_inbound_stage

        if update_inbound_stage(client_id, stage):
            logger.info("Sheet stage updated from HubSpot (ticket=%s -> %s)", ticket_id, stage)
    except Exception:
        logger.warning(
            "Sheet stage update failed for ticket %s (stage=%s)", ticket_id, stage, exc_info=True
        )


# Draft states that a HubSpot-side answer makes pointless. ``drafting`` is deliberately
# absent: that row is mid-flight in the inbound worker, which would write over this.
_SUPERSEDABLE = ("pending_approval", "draft_failed", "send_failed")


def _retire_superseded_drafts(session, conversation_id: int, local_stage: str) -> int:
    """Close drafts that a human already answered in HubSpot. Returns how many.

    Drafts are written for New tickets. When such a ticket turns up in a later stage it
    means someone replied in HubSpot while we were still holding an unsent draft — real
    work carried on during the pre-launch pause. Leaving the draft in 발송 대기 asks the
    operator to send an answer the customer has already received, and it is exactly why
    the queue shows rows whose Stage is not New.
    """
    from ..db.models import Message

    if local_stage == "new":
        return 0
    drafts = (
        session.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            Message.direction == "outgoing",
            Message.status.in_(_SUPERSEDABLE),
        )
        .all()
    )
    for draft in drafts:
        draft.status = "superseded"
    if drafts:
        add_progress(
            conversation_id,
            "draft",
            f"HubSpot에서 단계가 {local_stage}(으)로 이동해 대기 중이던 초안 "
            f"{len(drafts)}건을 종료 처리했습니다. 이미 답변이 나간 문의입니다.",
            session=session,
        )
    return len(drafts)


def sync_stage_from_hubspot(
    ticket_id: str | None,
    hubspot_stage_id: str | None,
    source: str = "hubspot",
) -> str | None:
    """Align the local conversation with a stage HubSpot reports.

    Returns the new local stage when something actually changed, else None — so
    callers can log or count real transitions without re-reporting no-ops.

    Also mirrors the move into the Google Sheet when that thread has a workbook row
    (see :func:`_mirror_stage_to_sheet`), so a stage someone drags in HubSpot lands
    in the sales sheet with no manual step.

    Silently ignores tickets we never ingested and stage ids that are not configured;
    both are normal (other pipelines share the same webhook).
    """
    local_stage = local_stage_for(hubspot_stage_id)
    if not ticket_id or not local_stage:
        return None

    with SessionLocal() as session:
        conv = (
            session.query(Conversation)
            .filter(Conversation.hubspot_ticket_id == str(ticket_id))
            .one_or_none()
        )
        if conv is None:
            return None
        if conv.stage == local_stage:
            return None

        previous = conv.stage
        conv.stage = local_stage
        # Read the workbook key while the session is open; the mirror runs after the
        # commit, and these instances are detached once the block exits.
        sheet_client_id = conv.sheet_client_id
        if not sheet_client_id and conv.contact_id:
            contact = session.get(Contact, conv.contact_id)
            sheet_client_id = contact.sheet_client_id if contact else None

        if conv.contact_id:
            profile = session.get(CustomerProfile, conv.contact_id)
            if profile is None:
                profile = CustomerProfile(contact_id=conv.contact_id)
                session.add(profile)
            profile.pipeline_stage = local_stage
            profile.customer_state = customer_state_for(local_stage, profile.customer_state)

        add_progress(
            conv.id,
            "stage",
            f"HubSpot에서 단계 변경 감지: {previous or '미지정'} → {local_stage} ({source}).",
            session=session,
        )
        retired = _retire_superseded_drafts(session, conv.id, local_stage)
        session.commit()

    logger.info(
        "Stage synced from HubSpot (ticket=%s, %s -> %s, source=%s, drafts_retired=%d)",
        ticket_id, previous, local_stage, source, retired,
    )
    # After the commit, so a Sheets failure can never roll back the local move.
    _mirror_stage_to_sheet(sheet_client_id, local_stage, str(ticket_id))
    return local_stage
