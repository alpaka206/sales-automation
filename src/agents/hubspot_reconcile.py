"""Bring our copy back in line with HubSpot, on demand.

The webhook is best-effort and the poller only sweeps tickets modified since its last
run, so two states drift and nothing notices:

  * a ticket answered or moved in HubSpot while we still hold an unsent draft — the
    operator is asked to send a reply the customer already received
  * a ticket DELETED in HubSpot — nothing in the poller looks for absence, so the thread
    stays on the board and its draft waits forever for a ticket that no longer exists.
    The deletion webhook fires once; if it was never subscribed or never arrived, this
    is the only thing that ever notices.

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


def _all_ticket_ids() -> list[tuple[int, str]]:
    """(conversation id, ticket id) for every thread we hold a HubSpot ticket for.

    Wider than :func:`_open_ticket_ids` on purpose, and only for the existence check.
    A thread that was already answered still draws a card on the board, so a ticket
    deleted in HubSpot stayed there forever: the deletion webhook is the only thing
    that ever hears about absence, the poller sweeps tickets HubSpot HAS, and 최신화
    used to ask only about threads with an unsent draft — which a 협상중 or Won card
    has none of.
    """
    with SessionLocal() as session:
        rows = (
            session.query(Conversation.id, Conversation.hubspot_ticket_id)
            .filter(Conversation.hubspot_ticket_id.is_not(None))
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


def _retire_drafts(conversation_id: int, local_stage: str) -> int:
    """Close the drafts on a thread HubSpot has already moved past New.

    Retired, not deleted, and the difference is deliberate: the ticket still EXISTS, so
    this is a real customer thread that someone answered elsewhere. Only a ticket that is
    gone from HubSpot earns a delete.
    """
    from .stage_sync import _retire_superseded_drafts

    with SessionLocal() as session:
        retired = _retire_superseded_drafts(session, conversation_id, local_stage)
        session.commit()
    return retired


def reconcile_with_hubspot(*, apply: bool = False) -> dict:
    """Realign every thread we are still holding an answer for. Returns a small report.

    Two different widths, and the difference is the point. **Existence** is checked for
    every ticket we hold: a deleted ticket has to leave the board too, and a card there
    has no draft to be found by. **Stage and stale drafts** stay narrow — only threads
    with an unsent draft, because those are the ones where being out of date costs
    something. A thread we already replied to can be wrong about its stage without
    anyone being asked to do the wrong thing, and the poller sweeps those anyway.

    ``apply=False`` is the default ON PURPOSE, and it matters more now that the second
    pass DELETES rather than retires. A stage move is reversible and gets applied either
    way. A delete is not, and 404 does not only mean "deleted" — it is also what a ticket
    id from another portal, a backfilled row, or an id we recorded wrong looks like.
    Testing this against a database of made-up ticket ids acted on three threads in one
    click. So the first pass only counts, and the operator confirms a number.
    """
    from .hubspot_backfill import B2B_PIPELINE_ID
    from .inbound_poller import reconcile_ticket_stages_once
    from .stage_sync import local_stage_for, sync_stage_from_hubspot

    report = {
        "checked": 0, "moved": 0, "deleted": 0, "retired": 0, "stale": 0, "swept": 0,
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

    # Absence, across every ticket we hold — one batch read per hundred, so asking about
    # the whole board costs about what asking about the draft queue used to. A batch that
    # fails raises, and then we report nothing deleted: "the token expired" must never be
    # read as "they are all gone".
    gone: set[int] = set()
    held = _all_ticket_ids()
    checked_in_batch = False
    try:
        alive = client.existing_ticket_ids_sync([ticket_id for _cid, ticket_id in held])
    except Exception as exc:
        logger.exception("Ticket existence check failed; skipping the deletion pass")
        # 화면에 적습니다. 조용히 건너뛰면 「정리할 항목 없음」이 두 가지 뜻을 갖습니다 —
        # 정말 없는 것과, 보지 못한 것. 운영자에게는 그 둘이 똑같이 보였습니다.
        report["error"] = f"삭제 검사를 건너뛰었습니다 — {type(exc).__name__}: {exc}"[:200]
    else:
        # 확인 N건 counts threads, not calls, and this pass already covered every one of
        # them — the loop below re-visits a subset and must not count them twice.
        checked_in_batch = True
        report["checked"] = len(held)
        for conversation_id, ticket_id in held:
            if ticket_id in alive:
                continue
            gone.add(conversation_id)
            report["deleted"] += 1
            if apply:
                delete_conversation(conversation_id, ticket_id)
                report["retired"] += 1

    # Then the part no sweep can do: ask about the tickets we are still waiting on, one
    # at a time — their stage, and the drafts that stage has already answered.
    for conversation_id, ticket_id in _open_ticket_ids():
        if conversation_id in gone:
            continue
        if not checked_in_batch:
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

        # **우리 파이프라인 밖으로 옮겨진 티켓.** 우리 관할이 아니게 된 문의라 티켓이
        # 지워진 것과 같이 다룹니다. 세는 것은 두 패스 다, 지우는 것은 apply 일 때만 —
        # 삭제는 되돌릴 수 없고, 이 화면의 규칙이 「먼저 세고 사람이 숫자를 확인한다」
        # 입니다. 자동 경로(웹훅·폴러)는 `stage_sync` 가 그때그때 처리합니다.
        if ticket.pipeline and str(ticket.pipeline) != B2B_PIPELINE_ID:
            report["deleted"] += 1
            if apply:
                delete_conversation(conversation_id, ticket_id)
                report["retired"] += 1
            continue

        # Applied in both passes: aligning to the stage HubSpot already shows is not a
        # loss, and it is the half of the drift that needs no judgement.
        if sync_stage_from_hubspot(ticket.id, ticket.pipeline_stage, source="manual"):
            report["moved"] += 1

        # After 최신화 only New keeps a waiting draft. Anything past it was answered in
        # HubSpot, so the draft is an answer the customer already has. sync_stage_from_
        # hubspot retires drafts when the stage MOVES; a thread that was already sitting
        # on a later stage never moved, so nothing ever cleared it — which is how these
        # accumulated in 발송 대기 in the first place.
        local_stage = local_stage_for(ticket.pipeline_stage)
        if local_stage and local_stage != "new":
            report["stale"] += 1
            if apply:
                report["retired"] += _retire_drafts(conversation_id, local_stage)

    return report
