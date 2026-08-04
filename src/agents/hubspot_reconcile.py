"""Bring our copy back in line with HubSpot, on demand.

The webhook is best-effort and the poller only sweeps tickets modified since its last
run, so two states drift and nothing notices:

  * a ticket answered or moved in HubSpot while we still hold an unsent draft — the
    operator is asked to send a reply the customer already received
  * a ticket DELETED in HubSpot — nothing in the poller looks for absence, so our draft
    waits forever for a thread that no longer exists

Both are read-mostly against HubSpot: the only writes are to our own tables, and every
one of them retires a draft rather than sending anything.
"""

from __future__ import annotations

import logging

import httpx

from ..db.models import Conversation, Message
from ..db.session import SessionLocal
from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured

logger = logging.getLogger(__name__)

# The statuses that mean "we are still holding an answer nobody has sent".
_UNSENT = ("pending_approval", "approved", "drafting", "draft_failed", "send_failed")


def _open_ticket_ids() -> list[tuple[int, str]]:
    """(conversation id, ticket id) for every thread still holding an unsent draft."""
    with SessionLocal() as session:
        rows = (
            session.query(Conversation.id, Conversation.hubspot_ticket_id)
            .join(Message, Message.conversation_id == Conversation.id)
            .filter(
                Conversation.hubspot_ticket_id.is_not(None),
                Message.direction == "outgoing",
                Message.status.in_(_UNSENT),
            )
            .distinct()
            .all()
        )
    return [(conv_id, str(ticket_id)) for conv_id, ticket_id in rows if ticket_id]


def delete_conversation(conversation_id: int, ticket_id: str) -> int:
    """The ticket is gone from HubSpot, so the thread goes here too.

    What goes: the conversation, its messages, its progress log. A thread whose ticket
    was deleted is a thread that should not have existed.

    What STAYS, and this is the part that matters:

      * the Contact — a real person who may have other inquiries
      * ContractRecord — money. A contract is never deleted because a ticket was; its
        conversation_id is ON DELETE SET NULL for exactly this
      * CustomerInteraction — the operator's own note about a meeting that really
        happened, likewise detached rather than destroyed

    Children are removed explicitly rather than left to the FK: the cascades are declared
    ON DELETE, which SQLite only honours with foreign_keys=ON and which the ORM would
    otherwise try to satisfy by nulling a NOT NULL column. Being explicit also makes the
    blast radius readable, which for a delete is the point.
    """
    from sqlalchemy import delete, update

    from ..db.models import ContractRecord, ConversationProgress, CustomerInteraction

    with SessionLocal() as session:
        if session.get(Conversation, conversation_id) is None:
            return 0
        removed = session.query(Message).filter(
            Message.conversation_id == conversation_id
        ).count()

        for model in (ContractRecord, CustomerInteraction):
            session.execute(
                update(model)
                .where(model.conversation_id == conversation_id)
                .values(conversation_id=None)
            )
        session.execute(
            delete(ConversationProgress).where(
                ConversationProgress.conversation_id == conversation_id
            )
        )
        session.execute(delete(Message).where(Message.conversation_id == conversation_id))
        session.execute(delete(Conversation).where(Conversation.id == conversation_id))
        session.commit()

    logger.info(
        "Deleted conversation %s (HubSpot ticket %s is gone); %d message(s) removed.",
        conversation_id, ticket_id, removed,
    )
    return removed


def delete_by_ticket(ticket_id: str) -> int:
    """Delete the thread for a ticket id, if we have one. For the webhook."""
    with SessionLocal() as session:
        conv = (
            session.query(Conversation)
            .filter(Conversation.hubspot_ticket_id == str(ticket_id))
            .one_or_none()
        )
        conversation_id = conv.id if conv else None
    if conversation_id is None:
        return 0
    delete_conversation(conversation_id, str(ticket_id))
    return 1


def reconcile_with_hubspot(*, apply: bool = False) -> dict:
    """Realign every thread we are still holding an answer for. Returns a small report.

    Deliberately narrow: it looks only at conversations with an unsent draft, because
    those are the ones where being out of date costs something. A thread we already
    replied to can be wrong about its stage without anyone being asked to do the wrong
    thing, and the poller sweeps those anyway.

    ``apply=False`` is the default ON PURPOSE, and it matters more now that the second
    pass DELETES rather than retires. A stage move is reversible and gets applied either
    way. A delete is not, and 404 does not only mean "deleted" — it is also what a ticket
    id from another portal, a backfilled row, or an id we recorded wrong looks like.
    Testing this against a database of made-up ticket ids acted on three threads in one
    click. So the first pass only counts, and the operator confirms a number.
    """
    from .inbound_poller import reconcile_ticket_stages_once
    from .stage_sync import sync_stage_from_hubspot

    report = {
        "checked": 0, "moved": 0, "deleted": 0, "retired": 0, "swept": 0,
        "applied": apply, "error": None,
    }

    try:
        client = HubSpotClient()
    except HubSpotNotConfigured:
        report["error"] = "HubSpot이 설정되지 않았습니다"
        return report

    # The existing sweep first: it catches stage moves across every ticket HubSpot
    # touched recently, which is cheap and covers most of the drift.
    try:
        report["swept"] = reconcile_ticket_stages_once()
    except Exception:
        logger.exception("Stage sweep failed during manual reconcile")

    # Then the part no sweep can do: ask about the tickets we are still waiting on, one
    # at a time, including the ones HubSpot no longer has.
    for conversation_id, ticket_id in _open_ticket_ids():
        report["checked"] += 1
        try:
            ticket = client.get_ticket_sync(ticket_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 410):
                report["deleted"] += 1
                if apply:
                    delete_conversation(conversation_id, ticket_id)
                    report["retired"] += 1
            else:
                # 401/403 means the token is the problem, not the ticket. Saying "this
                # ticket was deleted" then would be a lie with consequences.
                logger.warning("Ticket %s check failed: %s", ticket_id, exc)
            continue
        except Exception:
            logger.exception("Ticket %s check failed", ticket_id)
            continue

        # Applied in both passes: aligning to the stage HubSpot already shows is not a
        # loss, and it is the half of the drift that needs no judgement.
        if sync_stage_from_hubspot(ticket.id, ticket.pipeline_stage, source="manual"):
            report["moved"] += 1

    return report
