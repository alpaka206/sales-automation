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
from ..db.models import Conversation, CustomerProfile
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

# Local stage key -> the Settings attribute holding its HubSpot stage id.
# Ordered like the pipeline. Kept here (not in customer_ops) because both the web
# routes and the background agents need it, and importing a routes module from an
# agent would be a circular import.
LOCAL_STAGE_TO_SETTING: dict[str, str] = {
    "new": "HUBSPOT_TICKET_STAGE_NEW",
    "meeting_link_sent": "HUBSPOT_TICKET_STAGE_AFTER_SEND",
    "reminder_sent": "HUBSPOT_TICKET_STAGE_REMINDER_SENT",
    "follow_up_needed": "HUBSPOT_TICKET_STAGE_FOLLOW_UP_NEEDED",
    "negotiation": "HUBSPOT_TICKET_STAGE_NEGOTIATION",
    "won": "HUBSPOT_TICKET_STAGE_WON",
    "closed_lost": "HUBSPOT_TICKET_STAGE_CLOSED_LOST",
    "closed": "HUBSPOT_TICKET_STAGE_CLOSED",
    # Legacy local-only stages; their env vars are blank on the B2B pipeline.
    "contracted": "HUBSPOT_TICKET_STAGE_CONTRACTED",
    "onboarding": "HUBSPOT_TICKET_STAGE_ONBOARDING",
    "active": "HUBSPOT_TICKET_STAGE_ACTIVE",
}

# Local stages that imply the customer relationship has moved on. Mirrors the
# customer_state logic in customer_ops._set_local_stage so a HubSpot-driven move and
# an operator-driven move leave the profile in the same shape.
_STATE_FOR_STAGE: dict[str, str] = {
    "won": "service",
    "contracted": "service",
    "onboarding": "service",
    "active": "service",
    "closed_lost": "lost",
    "closed": "lost",
}


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


def sync_stage_from_hubspot(
    ticket_id: str | None,
    hubspot_stage_id: str | None,
    source: str = "hubspot",
) -> str | None:
    """Align the local conversation with a stage HubSpot reports.

    Returns the new local stage when something actually changed, else None — so
    callers can log or count real transitions without re-reporting no-ops.

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

        if conv.contact_id:
            profile = session.get(CustomerProfile, conv.contact_id)
            if profile is None:
                profile = CustomerProfile(contact_id=conv.contact_id)
                session.add(profile)
            profile.pipeline_stage = local_stage
            new_state = _STATE_FOR_STAGE.get(local_stage)
            if new_state:
                profile.customer_state = new_state
            elif profile.customer_state in {"service", "lost"}:
                # Moved back into an open stage — reopen instead of leaving it closed.
                profile.customer_state = "negotiation"

        add_progress(
            conv.id,
            "stage",
            f"HubSpot에서 단계 변경 감지: {previous or '미지정'} → {local_stage} ({source}).",
            session=session,
        )
        session.commit()

    logger.info(
        "Stage synced from HubSpot (ticket=%s, %s -> %s, source=%s)",
        ticket_id, previous, local_stage, source,
    )
    return local_stage
