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
from datetime import datetime, timezone

from ..db.conversation_history import add_progress
from ..db.models import Contact, Conversation, CustomerProfile
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

# Local stage key -> the Settings attribute holding its HubSpot stage id.
# Ordered like the pipeline. Kept here (not in customer_ops) because both the web
# routes and the background agents need it, and importing a routes module from an
# agent would be a circular import.
#
# **키는 우리 것이고, 화면에 보이는 이름은 HubSpot 것입니다.** 둘은 따로 움직입니다:
# "Meeting link sent" 는 "Qualified" 로, "Closed" 는 "Not a Fit" 으로 이름만 바뀌었고
# stage id 는 그대로입니다. 그래서 여기 키도 그대로 둡니다 — 키를 따라 바꾸면 대화·프로필
# 두 테이블의 값을 옮기는 마이그레이션이 필요하고, 다음에 이름이 또 바뀌면 그걸 또 합니다.
# 사람이 읽는 이름은 ``customer_ops.PIPELINE_STAGES`` 한 곳에만 있습니다.
LOCAL_STAGE_TO_SETTING: dict[str, str] = {
    "new": "HUBSPOT_TICKET_STAGE_NEW",
    "meeting_link_sent": "HUBSPOT_TICKET_STAGE_AFTER_SEND",       # 화면 이름: Qualified
    "negotiation": "HUBSPOT_TICKET_STAGE_NEGOTIATION",
    "reminder_sent": "HUBSPOT_TICKET_STAGE_REMINDER_SENT",
    "won": "HUBSPOT_TICKET_STAGE_WON",
    "closed_lost": "HUBSPOT_TICKET_STAGE_CLOSED_LOST",
    "no_response": "HUBSPOT_TICKET_STAGE_NO_RESPONSE",
    "closed": "HUBSPOT_TICKET_STAGE_CLOSED",                      # 화면 이름: Not a Fit
}

# Local stages that imply the customer relationship has moved on. THE one copy of this
# rule: customer_ops (operator move) and sheet_sync (workbook import) import it too, so
# all three paths leave the profile in the same shape. Before migration 0040 each kept
# its own divergent copy.
STATE_FOR_STAGE: dict[str, str] = {
    "won": "service",
    "closed_lost": "lost",
    # No Response 도 끝난 문의입니다 — 답이 없어 끝났을 뿐, 아직 협상 중인 건이 아닙니다.
    "no_response": "lost",
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
# absent: that row is mid-flight in the inbound worker, which would write over this — it
# lands here anyway the moment ``_finalize_draft`` stamps its terminal status, which is
# why that function calls this one. ``approved`` IS here: the send worker claims a row on
# status alone, so it was the one unsent state that could still mail a customer after the
# ticket had moved on.
_SUPERSEDABLE = ("pending_approval", "approved", "draft_failed", "send_failed")

# "New 를 벗어났다" 는 **매핑된 파이프라인 단계**일 때뿐입니다. 모델 기본값인 "initial",
# None, 뜻을 모르는 값은 단계가 움직인 것이 아닙니다 — `!= "new"` 로 세면 아직 아무도 손대지
# 않은 티켓의 초안까지 종료됩니다.
_PAST_NEW = frozenset(LOCAL_STAGE_TO_SETTING) - {"new"}


def _retire_superseded_drafts(session, conversation_id: int, local_stage: str) -> int:
    """Close drafts that the ticket has already moved past. Returns how many.

    Drafts are written for New tickets. When the ticket is in a later stage it means the
    inquiry was answered another way — someone replied in HubSpot, the operator dragged
    the card after a call, the meeting link went out. Leaving the draft in 발송 대기 asks
    the operator to send an answer the customer already has, and it is exactly why the
    queue used to show rows whose Stage is not New.

    **이 함수가 초안을 종료하는 유일한 곳입니다.** 단계를 옮기는 쪽(HubSpot 동기화, 콘솔
    보드, 워크북·백필 가져오기)과 초안을 완성하는 쪽(``_finalize_draft``)이 전부 여기로
    옵니다 — 화면·집계·발송이 모두 ``Message.status`` 하나만 보므로, 여기서 한 번 종료하면
    나머지는 따라옵니다.
    """
    from ..db.models import Message

    if local_stage not in _PAST_NEW:
        return 0
    drafts = (
        session.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            Message.direction == "outgoing",
            Message.status.in_(_SUPERSEDABLE),
            # 접수확인은 초안이 아닙니다. 사람이 검토하는 회신이 아니라 문의를 받았다는
            # 자동 응답이고, ``approved`` 로 발송 큐에 들어가 있습니다 — 여기서 걸러 내지
            # 않으면 단계 한 번 옮기는 것이 아직 안 나간 고객 접수확인을 취소합니다.
            # 목록·집계·검토 화면이 전부 두는 것과 같은 조건입니다.
            (Message.prompt_variant.is_(None)) | (Message.prompt_variant != "auto_ack"),
        )
        .all()
    )
    for draft in drafts:
        draft.status = "superseded"
    if drafts:
        add_progress(
            conversation_id,
            # Its own kind: 처리 경과 hides the routine "draft" entries, and a draft
            # retired out from under the operator is the opposite of routine.
            "draft_retired",
            # 어디서 옮겼는지는 적지 않습니다 — 바로 위에 그 단계 이동 기록이 있고,
            # 이제 옮기는 곳이 HubSpot 만이 아닙니다(콘솔 보드·워크북·백필).
            f"단계가 {local_stage}(으)로 이동해 대기 중이던 초안 {len(drafts)}건을 "
            f"종료 처리했습니다. 이미 답변이 나간 문의입니다.",
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
            # 단계는 그대로여도 초안은 그 사이에 생겼을 수 있습니다: 작성 중이던 초안이
            # 단계가 옮겨진 **뒤에** 완성되면, 그 대화에는 다시 아무 변화도 오지 않습니다.
            # 여기서 한 번 더 훑지 않으면 그 초안은 영영 발송 대기로 남습니다.
            if _retire_superseded_drafts(session, conv.id, local_stage):
                session.commit()
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
        # Won 으로 넘어온 건은 수주 전환 대기에 쌓습니다. 여기 다는 이유: 웹훅도 10분
        # 폴러도 수동 최신화도 전부 이 함수를 거칩니다. 감지 지점을 세 군데 두면 하나가
        # 조용히 빠집니다.
        if local_stage == WON_STAGE:
            _enqueue_pending_won(session, conv, sheet_client_id)
        session.commit()

    logger.info(
        "Stage synced from HubSpot (ticket=%s, %s -> %s, source=%s, drafts_retired=%d)",
        ticket_id, previous, local_stage, source, retired,
    )
    # After the commit, so a Sheets failure can never roll back the local move.
    _mirror_stage_to_sheet(sheet_client_id, local_stage, str(ticket_id))
    return local_stage


# 파이프라인이 여기 오면 수주입니다. 로컬 단계 이름이고, HubSpot 쪽 id 는 설정에 있습니다.
WON_STAGE = "won"


def _enqueue_pending_won(session, conv, sheet_client_id: int | None) -> None:
    """Won 티켓을 수주 전환 대기에 올립니다 — 계약 정보는 사람이 채웁니다.

    바로 수주 고객으로 만들지 않는 이유: 금액도 기간도 없는 고객이 활성 고객 수와 예상
    MRR 을 오염시킵니다. 그리고 Won → Negotiating 롤백은 대기에서 내리면 끝입니다.

    같은 티켓이 두 번 와도 한 줄입니다(ticket_id UNIQUE). 이미 처리해서 ``done`` 이 된
    티켓은 되살리지 않습니다 — 계약을 등록한 뒤 폴러가 그 티켓을 또 훑으면 대기 목록에
    유령이 돌아옵니다.
    """
    from ..db.models import PendingWon

    ticket_id = str(conv.hubspot_ticket_id or "").strip()
    if not ticket_id:
        return
    existing = (
        session.query(PendingWon).filter(PendingWon.ticket_id == ticket_id).one_or_none()
    )
    if existing is not None:
        return
    contact = session.get(Contact, conv.contact_id) if conv.contact_id else None
    session.add(
        PendingWon(
            ticket_id=ticket_id,
            company=(contact.company if contact else None) or (contact.full_name if contact else None),
            client_id=sheet_client_id,
            conversation_id=conv.id,
            # 수주 유형(MRR/PoC)은 담당자가 고릅니다. HubSpot 의 Won type 은 읽지
            # 않습니다 — 그 속성이 이 파이프라인에 있는지 확인되지 않았고, 없는 값을
            # 추측해 채우면 PoC 였던 건이 MRR 로 굳습니다.
            won_type=None,
            won_on=datetime.now(timezone.utc).date().isoformat(),
        )
    )
    logger.info("수주 전환 대기에 추가: ticket=%s client=%s", ticket_id, sheet_client_id)
