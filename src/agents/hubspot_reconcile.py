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


def _close_deleted(conversation_id: int, ticket_id: str) -> int:
    """The ticket is gone from HubSpot. Retire what we were holding for it.

    Retired, not deleted: the row is what answers "why did this customer never get a
    reply" months later, and it costs nothing to keep. What matters to the operator is
    that it leaves every queue, which `superseded` and the closed stage both do.
    """
    from .stage_sync import add_progress

    with SessionLocal() as session:
        drafts = (
            session.query(Message)
            .filter(
                Message.conversation_id == conversation_id,
                Message.direction == "outgoing",
                Message.status.in_(_UNSENT),
            )
            .all()
        )
        for draft in drafts:
            draft.status = "superseded"
        conv = session.get(Conversation, conversation_id)
        if conv is not None:
            conv.stage = "closed"
        if drafts:
            add_progress(
                conversation_id,
                "draft_retired",
                f"HubSpot에서 티켓 {ticket_id}이(가) 삭제되어 대기 중이던 초안 "
                f"{len(drafts)}건을 종료 처리했습니다.",
                session=session,
            )
        session.commit()
    return len(drafts)


def reconcile_with_hubspot(*, apply: bool = False) -> dict:
    """Realign every thread we are still holding an answer for. Returns a small report.

    Deliberately narrow: it looks only at conversations with an unsent draft, because
    those are the ones where being out of date costs something. A thread we already
    replied to can be wrong about its stage without anyone being asked to do the wrong
    thing, and the poller sweeps those anyway.

    ``apply=False`` is the default ON PURPOSE, and it is the difference between this
    being safe and being a footgun. A stage move is reversible and gets applied either
    way. Retiring a draft because HubSpot answered 404 is not, and 404 does not only mean
    "deleted" — it is also what a ticket id from another portal, a backfilled row, or an
    id we recorded wrong looks like. Testing this against a database of made-up ticket
    ids retired three real drafts in one click, which is exactly the accident an operator
    would have had. So the first pass only counts, and the operator confirms.
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
                    report["retired"] += _close_deleted(conversation_id, ticket_id)
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
