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

# 사라진 티켓에서 옮겨 온 메일의 표. **화면이 이 글자로 그 기록들을 알아봅니다** — 티켓 행이
# 지워져(`delete(Conversation)`) `conversation_id` 로는 어느 티켓이었는지 알 수 없으므로,
# 남은 단서가 이 표와 메일 제목뿐입니다. 리드 히스토리가 그 둘로 「지난 티켓 · <제목>」
# 묶음을 다시 세웁니다(`messages._customer_history`). 글자를 바꾸면 그 묶음이 조용히
# 「티켓 외」로 흩어지므로 **여기 한 곳에서만** 정합니다.
PAST_TICKET_HANDLER = "(지난 티켓)"

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


def _still_has_something(session, contact_id: int, conversation_id: int, sheet_client_id) -> bool:
    """이 티켓이 사라진 뒤에도 그 연락처에 남는 것이 있나.

    운영자 규칙(2026-08-19): **수주 고객도 아니고 다른 티켓도 없으면 히스토리에서도 지운다.**
    거기에 두 가지를 더 붙입니다 — 사람이 손으로 쓴 메모(`CustomerInteraction`)와 계약
    기록(`ContractRecord`). 둘 다 티켓이 아니라 사람에게 달린 기록이고, 그것을 지우는 것은
    「티켓이 사라졌다」가 시키는 일이 아닙니다.

    수주 고객은 두 방향으로 찾습니다. `clients.contact_id` 는 정식 연결이지만 **운영 DB 에서
    29명 중 0명만 채워져 있어서**(콘솔 고객 추가 폼이 그 값을 안 받습니다) 그것만 보면 수주
    고객의 연락처도 지워집니다. Client ID 는 문의와 고객이 같은 번호대를 쓰므로
    (`conversations.sheet_client_id` ↔ `clients.client_id`) 그쪽으로도 맞춰 봅니다.
    """
    from ..db.models import Client, ContractRecord, CustomerInteraction

    others = (
        session.query(Conversation.id)
        .filter(Conversation.contact_id == contact_id, Conversation.id != conversation_id)
        .first()
    )
    if others:
        return True
    if session.query(CustomerInteraction.id).filter_by(contact_id=contact_id).first():
        return True
    if session.query(ContractRecord.id).filter_by(contact_id=contact_id).first():
        return True
    if session.query(Client.client_id).filter_by(contact_id=contact_id).first():
        return True
    if sheet_client_id and session.query(Client.client_id).filter_by(
        client_id=sheet_client_id
    ).first():
        return True
    return False


def _archive_messages(session, contact_id: int, conversation_id: int) -> int:
    """그 티켓의 메일을 **연락처 단위 히스토리로 옮겨 담습니다.**

    `customer_interactions` 는 처음부터 「소통 히스토리만 예외로 고객 단위」인 표라, 티켓이
    사라져도 남는 유일한 자리입니다. 옮겨 두면 리드 히스토리 타임라인이 지금 쓰는 조회를
    그대로 쓰면서 옛 메일을 계속 보여 줍니다 — 보드·집계 쿼리는 한 줄도 안 바뀝니다.

    `conversation_id` 는 일부러 비웁니다: 그 대화 행은 이 함수 직후에 사라지고, 남겨 두면
    없는 행을 가리키는 값이 됩니다.
    """
    from ..db.models import DELIVERED_STATUSES, CustomerInteraction

    # **이미 뽑아 둔 요약을 물려줍니다.** 접수할 때 모델이 만든 「고객 요청사항」이 그
    # 대화에 있습니다(`conversations.customer_requests`). 티켓이 사라져도 그 한 줄은
    # 남아야 나중에 「이때 무슨 이야기였나」를 펼쳐 보지 않고 알 수 있습니다 — 없는 것을
    # 다시 만들려고 모델을 부르는 대신, 있는 것을 가져다 씁니다.
    conversation = session.get(Conversation, conversation_id)
    digest = (conversation.customer_requests or conversation.summary) if conversation else None

    messages = [
        message
        for message in (
            session.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .all()
        )
        # **나가지 않은 것은 히스토리가 아닙니다** (2026-08-19 운영자 지시). 검토 대기로
        # 남아 있던 초안, 종료된 초안, 발송 실패는 「이 고객과 오간 것」이 아니라 우리
        # 안에서만 있던 문서입니다. 그걸 히스토리에 넣으면 나중에 그 대화를 읽는 사람이
        # 보낸 적 없는 답변을 보낸 것으로 셉니다. 받은 메일은 무조건 남깁니다 — 그건
        # 고객이 실제로 보낸 것입니다.
        if message.direction == "inbound" or message.status in DELIVERED_STATUSES
    ]
    for message in messages:
        session.add(
            CustomerInteraction(
                contact_id=contact_id,
                conversation_id=None,
                channel=message.channel or "email",
                direction=message.direction or "note",
                handler=PAST_TICKET_HANDLER,
                subject=message.subject,
                # NOT NULL 입니다. 본문 없는 메일은 없지만, 있다면 빈 문자열보다 이 편이
                # 화면에서 「무엇이 있었는지」를 말해 줍니다.
                summary=message.body or "(본문 없음)",
                # 받은 문의에만 붙입니다 — 요청사항은 고객이 물은 것을 줄인 값이라,
                # 우리가 보낸 답변 옆에 두면 그 답변의 요약인 것처럼 읽힙니다.
                context=digest if message.direction == "inbound" else None,
                happened_at=message.sent_at or message.created_at,
            )
        )
    if messages:
        logger.info(
            "티켓의 메일 %d통을 연락처 %s 의 히스토리로 옮겼습니다.", len(messages), contact_id
        )
    return len(messages)


def delete_conversation(conversation_id: int, ticket_id: str) -> int:
    """The ticket is gone from HubSpot, so the thread goes here too.

    What goes: the conversation, its messages, its progress log. A thread whose ticket
    was deleted is a thread that should not have existed.

    What STAYS, and this is the part that matters:

      * ContractRecord — money. A contract is never deleted because a ticket was; its
        conversation_id is ON DELETE SET NULL for exactly this
      * CustomerInteraction — the operator's own note about a meeting that really
        happened, likewise detached rather than destroyed
      * **주고받은 메일** — 그 연락처에 남는 것이 있으면(다른 티켓·수주 고객·메모·계약)
        연락처 단위 히스토리로 옮겨 담습니다(`_archive_messages`). 예전에는 티켓과 함께
        지워졌고, 그래서 리드 히스토리를 열면 「이전 기록이 하나도 없는」 고객이 남았습니다.

    그리고 **아무것도 안 남으면 연락처까지 지웁니다** (2026-08-19 운영자 지시: 「수주와
    다른 티켓이 없으면 히스토리에서도 지운다」). 그러지 않으면 대화도 계약도 메모도 없는
    빈 연락처가 리드 히스토리 목록에 계속 서 있습니다 — 실제로 그런 행이 있었습니다.

    Children are removed explicitly rather than left to the FK: the cascades are declared
    ON DELETE, which SQLite only honours with foreign_keys=ON and which the ORM would
    otherwise try to satisfy by nulling a NOT NULL column. Being explicit also makes the
    blast radius readable, which for a delete is the point.
    """
    from sqlalchemy import delete, select, update

    from ..db.models import (
        Approval,
        Contact,
        ContractRecord,
        ConversationProgress,
        CustomerInteraction,
    )

    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            return 0
        # 워크북 행을 찾는 자연키. 세션이 닫히면 못 읽으므로 지우기 전에 들고 나옵니다.
        sheet_client_id = conversation.sheet_client_id
        sheet_inquiry_key = conversation.sheet_inquiry_key
        contact_id = conversation.contact_id
        removed = session.query(Message).filter(
            Message.conversation_id == conversation_id
        ).count()

        # **먼저 정합니다: 이 연락처가 남을 사람인가.** 메일을 옮겨 담은 뒤에 세면 방금
        # 만든 히스토리가 「남을 이유」가 되어 빈 연락처도 영영 안 지워집니다.
        keeps = _still_has_something(session, contact_id, conversation_id, sheet_client_id)
        if keeps:
            _archive_messages(session, contact_id, conversation_id)

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
        # **승인 기록을 먼저 지웁니다.** 모델에는 `ondelete="CASCADE"` 라고 적혀 있지만
        # **운영 DB 의 제약에는 그것이 없습니다** — 그 표를 만든 옛 마이그레이션이 안 걸었고,
        # 모델의 선언은 이미 만들어진 제약을 바꾸지 않습니다. 그래서 승인을 한 번이라도 받은
        # 초안이 있는 대화를 지우려 하면 그 자리에서 죽었습니다:
        # `ForeignKeyViolation: ... still referenced from table "approvals"` (2026-09-03,
        # 운영자가 「허브스팟 최신화」를 눌러 500). 제약을 고치는 대신 여기서 지우는 이유는
        # **어느 DB 에서도 같게 동작하기 때문**입니다 — 제약의 상태에 기대지 않습니다.
        session.execute(
            delete(Approval).where(
                Approval.message_id.in_(
                    select(Message.id).where(Message.conversation_id == conversation_id)
                )
            )
        )
        session.execute(delete(Message).where(Message.conversation_id == conversation_id))
        session.execute(delete(Conversation).where(Conversation.id == conversation_id))
        if not keeps:
            # 프로필은 연락처와 함께 갑니다(PK 가 contact_id, ON DELETE CASCADE).
            session.execute(delete(Contact).where(Contact.id == contact_id))
            logger.info("빈 연락처 %s 도 같이 지웠습니다 — 남은 티켓도 수주도 없습니다.", contact_id)
        session.commit()

    logger.info(
        "Deleted conversation %s (HubSpot ticket %s is gone); %d message(s) removed.",
        conversation_id, ticket_id, removed,
    )
    # **워크북에서도 같이 사라집니다.** 안 그러면 같은 문의를 콘솔은 없다고 하고 시트는
    # 있다고 해서 두 화면의 건수가 영영 안 맞습니다. 시트 쪽이 실패해도 로컬 삭제는
    # 되돌리지 않습니다 — 이 함수는 「우리 것이 아니다」를 확인한 뒤에 불립니다.
    if sheet_client_id:
        from ..integrations.google_sheets import delete_inbound_row

        if sheet_inquiry_key:
            delete_inbound_row(sheet_client_id, sheet_inquiry_key)
        else:
            delete_inbound_row(sheet_client_id)
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
