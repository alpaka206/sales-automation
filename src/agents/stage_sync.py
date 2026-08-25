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

from sqlalchemy import select

from ..db.models import Contact, Conversation, CustomerProfile
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

# Local stage key -> the Settings attribute holding its HubSpot stage id.
# Ordered like the pipeline. Kept here (not in customer_ops) because both the web
# routes and the background agents need it, and importing a routes module from an
# agent would be a circular import.
#
# **키는 우리 것이고, 화면에 보이는 이름은 HubSpot 것입니다.** 둘은 따로 움직입니다:
# "Meeting link sent" 는 "Qualified" 로, "Closed" 는 "Not a Fit" 을 거쳐 "Concluded" 로 이름만 바뀌었고
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
    "closed": "HUBSPOT_TICKET_STAGE_CLOSED",                      # 화면 이름: Concluded
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


def _mirror_stage_to_sheet(
    client_id: int | None, stage: str, ticket_id: str, inquiry_key: str | None = None
) -> None:
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

        if update_inbound_stage(client_id, stage, inquiry_key=inquiry_key):
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
# 뜻을 아는 단계 전부. 「Won 에서 벗어났다」를 재는 자입니다 — `!= "won"` 으로 세면 모델
# 기본값 `initial` 이나 아직 매핑되지 않은 값까지 「벗어났다」가 되어, 아무도 손대지 않은
# 티켓의 대기 카드를 내려 버립니다.
_MAPPED_STAGES = frozenset(LOCAL_STAGE_TO_SETTING)


def _retire_superseded_drafts(session, conversation_id: int, local_stage: str) -> int:
    """티켓이 New 를 벗어나면 **나가지 않은 초안을 지웁니다.** 지운 수를 돌려줍니다.

    초안은 New 티켓에 대해 씁니다. 단계가 넘어갔다는 것은 답이 다른 경로로 나갔다는 뜻이고
    (허브스팟에서 회신했거나, 통화 뒤 카드를 옮겼거나, 미팅 링크가 나갔거나), 그 초안을
    발송 대기에 두면 고객이 이미 받은 답을 한 번 더 보내라고 청하는 셈입니다.

    **지웁니다, 닫지 않습니다** (2026-08-19 운영자 지시). 예전에는 ``superseded`` 로
    상태만 바꿔 행을 남겼는데, 그러면 나가지도 않은 초안이 히스토리에 남아 나중에 읽는
    사람이 「이 답변은 나갔다」로 셉니다. 고객이 본 적 없는 글은 그 대화의 기록이 아닙니다.
    **고쳐서 보낸 초안은 안전합니다** — 그건 ``sent`` 가 되어 아래 목록에 애초에 안 걸립니다.

    **이 함수가 초안을 없애는 유일한 곳입니다.** 단계를 옮기는 쪽(HubSpot 동기화, 콘솔
    보드, 워크북·백필 가져오기)과 초안을 완성하는 쪽(``_finalize_draft``)이 전부 여기로
    옵니다 — 한 곳에서 지우면 화면·집계·발송이 따로 확인할 것이 없습니다.

    **그래서 수주 전환 대기를 내리는 것도 여기서 합니다.** 단계가 옮겨졌을 때 그 단계에
    안 맞는 것을 치우는 자리가 이미 여기이고, 여덟 군데가 전부 이 함수를 지납니다. 옮기는
    곳마다 따로 달면 하나가 조용히 빠지고, 그러면 그 경로로 옮긴 건만 카드가 남습니다.
    돌려주는 수는 **초안만** 셉니다 — ``_finalize_draft`` 가 이 값으로 「내 초안이 밀렸나」를
    판단하므로(``inbound.py``), 대기를 내린 것까지 더하면 멀쩡한 초안이 밀린 것이 됩니다.
    """
    _retire_pending_won(session, conversation_id, local_stage)
    if local_stage not in _PAST_NEW:
        return 0
    return _delete_pending_drafts(
        session, conversation_id, why=f"단계 {local_stage} 이동"
    )


def _retire_pending_won(session, conversation_id: int, local_stage: str) -> bool:
    """Won 에서 벗어난 티켓을 수주 전환 대기에서 내립니다.

    Won 이 아니게 되었다는 것은 계약 정보를 받을 일이 없어졌다는 뜻입니다. 그대로 두면
    「계약 정보를 입력해야 합니다」 카드가 영영 남고, 운영자는 그것이 살아 있는 일감인지
    되돌려진 건인지 화면만 봐서는 모릅니다.

    ``done`` 이 아니라 ``dismissed`` 입니다. ``done`` 은 「계약을 받았다」는 뜻이라, 그것으로
    닫으면 그 티켓이 다시 Won 이 되어도 카드가 안 돌아옵니다. ``dismissed`` 는 「지금 Won 이
    아니다」이고, 다시 Won 이 되면 ``_enqueue_pending_won`` 이 ``pending`` 으로 되살립니다.
    """
    from ..db.models import PendingWon

    if local_stage == WON_STAGE or local_stage not in _MAPPED_STAGES:
        return False
    rows = (
        session.query(PendingWon)
        .filter(
            PendingWon.conversation_id == conversation_id,
            PendingWon.status == "pending",
        )
        .all()
    )
    for row in rows:
        row.status = "dismissed"
        _retire_empty_client(session, row, conversation_id)
        logger.info(
            "수주 전환 대기에서 내렸습니다 (ticket=%s, stage=%s)", row.ticket_id, local_stage
        )
    return bool(rows)


def _retire_empty_client(session, row, conversation_id: int) -> None:
    """물러난 대기 건이 남긴 **계약 없는 고객**을 장부에서 내립니다.

    Won 이 아니게 된 문의가 「세팅중」 고객으로 목록과 워크북 「고객 기본 정보」에 남으면
    활성 고객 수가 부풀고, 치우는 길은 사람이 상세 화면을 찾아 들어가 누르는 것뿐입니다.

    **지우지 않고 내립니다** (2026-08-25 운영자 지시). 지우면 Client ID 가 같이 사라지는데,
    그 번호는 문의·연락처가 들고 있고 워크북의 계약·회차 탭과 Inbound DB 가 그 행을 조회해
    회사명을 가져옵니다 — 한 건이 Won 에서 물러났다고 그 연결을 끊을 이유가 없습니다.
    내림은 되돌릴 수 있고(``POST /won-customers/{id}/retire``), 계약이 들어오면 저절로
    되돌아옵니다(``_add_contract`` 가 ``retired_on`` 을 비웁니다).

    **계약이 하나라도 있으면 손대지 않습니다** — 금액·크레딧·인식 매출이 붙은 행입니다.
    그 외에는 조건을 두지 않습니다: 그 번호를 다른 문의가 같이 쓰고 있더라도 계약이 없는
    고객은 어느 쪽에서 보아도 활성 고객이 아니고, 그 문의가 나중에 수주되면 계약을 저장하는
    순간 다시 올라옵니다. 지우던 시절에는 그 경우를 막아야 했습니다 — 되돌릴 수 없어서요.
    """
    from ..db.models import Client

    client_id = row.client_id
    if not client_id:
        return
    client = session.get(Client, client_id)
    if client is None or client.contracts or client.retired_on:
        return
    client.retired_on = datetime.now(timezone.utc).date().isoformat()
    logger.info(
        "계약이 없는 수주 고객 %s 를 장부에서 내렸습니다 (ticket=%s)", client_id, row.ticket_id
    )


def retire_drafts_answered_elsewhere(session, conversation_id: int) -> int:
    """허브스팟에 **우리가 보낸 메일**이 잡히면 그 답은 이미 나갔습니다 — 초안을 지웁니다.

    단계와 무관합니다. 영업이 허브스팟에서 직접 회신하면 티켓이 New 에 그대로 있는 일이
    흔하고(카드를 옮기는 것은 나중이거나 아예 안 합니다), 그동안 우리 초안은 발송 대기에
    남아 있습니다. 그걸 누르면 고객은 같은 질문에 두 번째 답을 받습니다.

    지우는 일 자체는 `_delete_pending_drafts` 한 곳입니다 — 단계 이동이든 이쪽이든
    초안이 없어지는 길은 하나여야 화면·집계·발송이 따로 확인할 것이 없습니다.
    """
    return _delete_pending_drafts(session, conversation_id, why="허브스팟 발송 기록 확인")


def _delete_pending_drafts(session, conversation_id: int, *, why: str) -> int:
    """나가지 않은 초안과 그 승인 기록을 지웁니다. 지운 수를 돌려줍니다."""
    from sqlalchemy import delete as sql_delete

    from ..db.models import Approval, Message

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
        )
        .all()
    )
    if drafts:
        ids = [draft.id for draft in drafts]
        # 승인 기록이 먼저입니다. FK 는 ON DELETE CASCADE 지만 SQLite 는 `foreign_keys=ON`
        # 일 때만 지키고, ORM 은 NOT NULL 인 열을 비우려 들어 터집니다 — 지우는 범위는
        # 눈에 보이는 편이 낫기도 합니다(`delete_conversation` 과 같은 이유).
        session.execute(sql_delete(Approval).where(Approval.message_id.in_(ids)))
        session.execute(sql_delete(Message).where(Message.id.in_(ids)))
        # **기록을 남기지 않습니다** (2026-08-20 운영자 지시). 나가지 않은 초안이
        # 없어졌다는 사실은 이 고객과 오간 일이 아니라 우리 안의 사정이고, 히스토리는
        # 「무엇이 오갔나」를 보는 자리입니다. 로그에는 남습니다 — 아래 호출부가
        # 지운 수를 세어 로그에 적습니다.
        logger.info(
            "%s(으)로 대기 중이던 초안 %d건을 지웠습니다 (conversation=%s).",
            why, len(drafts), conversation_id,
        )
    return len(drafts)



def _ticket_pipeline(ticket_id: str) -> str | None:
    """그 티켓이 **지금** 어느 파이프라인에 있나. 못 물어보면 None.

    None 은 「모른다」이지 「나갔다」가 아닙니다 — 부르는 쪽은 모르면 아무것도 지우지
    않습니다. 토큰이 만료됐거나 허브스팟이 잠깐 죽은 것을 「우리 관할이 아니게 됐다」로
    읽으면, 한 번 삐끗한 사이에 보드가 통째로 비워집니다.
    """
    try:
        from ..integrations.hubspot import HubSpotClient

        ticket = HubSpotClient().get_ticket_sync(str(ticket_id))
    except Exception:
        logger.warning("티켓 %s 의 파이프라인을 확인하지 못했습니다.", ticket_id, exc_info=True)
        return None
    return getattr(ticket, "pipeline", None)


def _handle_unmapped_stage(
    ticket_id: str | None, hubspot_stage_id: str | None, source: str
) -> None:
    """매핑에 없는 stage id 로 옮겨진 티켓. 세 가지인데 하는 일이 각각 다릅니다.

    ① **우리 티켓이 다른 파이프라인으로 넘어갔다.** 우리 관할이 아니게 된 문의입니다.
       티켓이 지워졌을 때와 같은 처리를 합니다(`delete_conversation` — 대화·메시지는
       지우고 연락처·계약·소통 히스토리는 남깁니다). 안 지우면 **영영 남습니다**: 10분
       스윕은 우리 파이프라인만 검색하므로 나간 티켓을 다시 만나지 못하고, 웹훅은 stage
       id 만 들고 오므로 파이프라인이 바뀐 것 자체를 알 수 없습니다. 되돌아오면 스윕이
       모르는 티켓으로 다시 주워 옵니다(`hubspot_backfill.adopt_ticket`).
    ② **애초에 남의 파이프라인 티켓.** 웹훅은 포털 전체를 보내므로 대부분 이쪽입니다.
       조용히 넘어갑니다 — 예전에는 이것까지 경고라 로그가 남의 티켓으로 가득 찼고,
       그래서 정작 ③ 이 안 보였습니다.
    ③ **우리 파이프라인인데 설정에 없는 단계.** 그 단계로의 이동이 전부 조용히 사라지는
       상태라 경고를 남깁니다. **stage id 는 적지 않습니다** — `/logs` 의 스크러버가
       9자리 이상 숫자를 전화번호로 보고 지웁니다(`common/log_buffer.py`). 대신 바로
       행동이 되는 사실을 적습니다: 어느 로컬 단계가 설정에서 빠졌는가.
    """
    if not ticket_id or not hubspot_stage_id:
        return None

    with SessionLocal() as session:
        conversation_id = session.scalar(
            select(Conversation.id).where(Conversation.hubspot_ticket_id == str(ticket_id))
        )
    if conversation_id is None:
        logger.info(
            "Unmapped stage reported for ticket %s (source=%s); we do not carry it.",
            ticket_id, source,
        )
        return None

    from .hubspot_backfill import B2B_PIPELINE_ID

    pipeline = _ticket_pipeline(ticket_id)
    if pipeline and str(pipeline) != B2B_PIPELINE_ID:
        from .hubspot_reconcile import delete_conversation

        # **티켓 번호가 아니라 문의 번호를 적습니다.** `/logs` 의 스크러버가 9자리 이상
        # 숫자를 전화번호로 보고 지우는데(`common/log_buffer.py`) 허브스팟 티켓 번호가 딱
        # 거기 걸립니다 — 「티켓 [REDACTED_PHONE] 를 내렸습니다」는 아무것도 안 알려 줍니다.
        # 문의 번호는 짧아 살아남고, 지운 뒤에 그것이 무엇이었는지 찾는 유일한 단서입니다.
        logger.warning(
            "문의 #%s 의 티켓이 우리 파이프라인 밖으로 옮겨졌습니다 (source=%s). 목록에서 내립니다.",
            conversation_id, source,
        )
        delete_conversation(conversation_id, str(ticket_id))
        return None

    missing = sorted(set(LOCAL_STAGE_TO_SETTING) - set(stage_id_to_local().values()))
    logger.warning(
        "HubSpot 이 우리가 모르는 단계로 티켓 %s 를 옮겼습니다 (source=%s). "
        "id 가 설정되지 않은 단계: %s",
        ticket_id, source, ", ".join(missing) or "(없음 — 파이프라인 확인 실패)",
    )
    return None


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
    if not local_stage:
        return _handle_unmapped_stage(ticket_id, hubspot_stage_id, source)
    if not ticket_id:
        return None

    with SessionLocal() as session:
        conv = (
            session.query(Conversation)
            .filter(Conversation.hubspot_ticket_id == str(ticket_id))
            .one_or_none()
        )
        if conv is None:
            # 우리가 안 들여온 티켓입니다. 흔한 일이지만(다른 파이프라인) 흔적은 남깁니다 —
            # 「우리 티켓인데 안 따라온다」와 구별할 수 있어야 합니다.
            logger.info(
                "Stage %s reported for ticket %s but no conversation carries that id (source=%s)",
                local_stage, ticket_id, source,
            )
            return None

        # 워크북 키는 세션이 열려 있는 동안 읽습니다. 미러는 커밋 뒤에 돌고, 블록을 벗어나면
        # 이 인스턴스들은 detached 입니다.
        #
        # Client ID는 회사가 공유하므로 그것만으로 행을 고르면 안 됩니다. 문의별
        # `sheet_inquiry_key`를 함께 넘겨 정확한 워크북 행만 갱신합니다.
        sheet_client_id = conv.sheet_client_id
        sheet_inquiry_key = conv.sheet_inquiry_key

        # **프로필은 이 대화가 그 연락처의 최신일 때만 씁니다.** `CustomerProfile` 은 연락처당
        # 하나인데 한 연락처에 문의가 여럿일 수 있어서, 옛 티켓이 움직일 때마다 화면 값이 그
        # 옛 티켓으로 끌려갔습니다. 콘솔 쪽 이동은 이미 같은 규칙입니다
        # (`customer_ops._set_conversation_stage`).
        profile = None
        if conv.contact_id:
            newest_id = session.scalar(
                select(Conversation.id)
                .where(Conversation.contact_id == conv.contact_id)
                .order_by(Conversation.created_at.desc(), Conversation.id.desc())
                .limit(1)
            )
            if newest_id == conv.id:
                profile = session.get(CustomerProfile, conv.contact_id)
                if profile is None:
                    profile = CustomerProfile(contact_id=conv.contact_id)
                    session.add(profile)

        # **단계 값이 사는 열은 둘입니다.** `Conversation.stage`(문의별)와
        # `CustomerProfile.pipeline_stage`(연락처별). 화면은 자리마다 다른 쪽을 읽습니다 —
        # 보드는 앞엣것, 리드 히스토리·고객 상세는 뒤엣것. 예전에는 `conv.stage` 하나만 보고
        # 「바뀐 것 없음」이라 판단해 되돌아갔는데, 그러면 둘이 한 번 어긋난 뒤로는 **영영**
        # 안 맞습니다: 이후 모든 스윕이 같은 자리에서 되돌아가 프로필을 못 고칩니다. 그리고
        # 실제로 어긋납니다 — 발송 워커는 `conv.stage` 만, 고객 상세 폼은 프로필만 씁니다.
        # 허브스팟이 기준이므로 여기서 **둘 다** 맞춥니다.
        stage_changed = conv.stage != local_stage
        profile_changed = profile is not None and profile.pipeline_stage != local_stage
        if not stage_changed and not profile_changed:
            # 단계는 그대로여도 초안은 그 사이에 생겼을 수 있습니다: 작성 중이던 초안이
            # 단계가 옮겨진 **뒤에** 완성되면, 그 대화에는 다시 아무 변화도 오지 않습니다.
            # 여기서 한 번 더 훑지 않으면 그 초안은 영영 발송 대기로 남습니다.
            # 돌려주는 수는 초안만 세지만, 이 함수는 그 단계에 안 맞는 수주 전환 대기도
            # 같이 내립니다. 그래서 「바뀐 것이 있을 때만」 커밋하지 않고 늘 커밋합니다 —
            # 아무것도 안 바뀐 커밋은 공짜이고, 조건을 달면 대기를 내린 것이 조용히
            # 사라집니다(그 값은 초안 수에 안 실립니다).
            _retire_superseded_drafts(session, conv.id, local_stage)
            # Won 은 다른 경로(콘솔·워크북)가 먼저 옮겼을 수 있습니다. 그때 수주 전환 대기가
            # 비어 있으면 10분마다 도는 이 스윕이 유일한 복구 기회입니다.
            if local_stage == WON_STAGE:
                _enqueue_pending_won(session, conv, sheet_client_id)
            session.commit()
            # 워크북 미러는 실패해도 아무 데도 안 남습니다. 이 스윕이 곧 재시도라서, 값이
            # 같아도 한 번 더 밀어 둡니다 — 시트만 뒤처져 있던 경우가 여기서 복구됩니다.
            _mirror_stage_to_sheet(
                sheet_client_id, local_stage, str(ticket_id), sheet_inquiry_key
            )
            return None

        previous = conv.stage
        conv.stage = local_stage
        if profile is not None:
            profile.pipeline_stage = local_stage
            profile.customer_state = customer_state_for(local_stage, profile.customer_state)

        # **단계 이동은 기록으로 남기지 않습니다** (2026-08-20 운영자 지시). 옮겨지면
        # 우리 DB·워크북·허브스팟의 **상태만** 바뀌면 됩니다. 예전에는 여기서 진행 기록을
        # 한 줄 남겼는데, 화면에서는 이미 숨기고 있었고(`_ROUTINE_PROGRESS_KINDS`) 지금
        # 단계는 Stage 칸이 그대로 보여 줍니다 — 아무도 안 읽는 줄을 대화마다 쌓고
        # 있었습니다. `previous` 는 아래 워크북 미러가 「바뀌었나」를 판단하는 데 씁니다.
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
    _mirror_stage_to_sheet(sheet_client_id, local_stage, str(ticket_id), sheet_inquiry_key)
    return local_stage


# 파이프라인이 여기 오면 수주입니다. 로컬 단계 이름이고, HubSpot 쪽 id 는 설정에 있습니다.
WON_STAGE = "won"


def _enqueue_pending_won(session, conv, sheet_client_id: int | None) -> bool:
    """Won 티켓을 수주 전환 대기에 올립니다 — 계약 정보는 사람이 채웁니다.

    바로 수주 고객으로 만들지 않는 이유: 금액도 기간도 없는 고객이 활성 고객 수와 예상
    MRR 을 오염시킵니다. 그리고 Won → Negotiating 롤백은 대기에서 내리면 끝입니다.

    같은 티켓이 두 번 와도 한 줄입니다(ticket_id UNIQUE). 이미 처리해서 ``done`` 이 된
    티켓은 되살리지 않습니다 — 계약을 등록한 뒤 폴러가 그 티켓을 또 훑으면 대기 목록에
    유령이 돌아옵니다.

    **그 티켓의 계약이 이미 장부에 있으면 대기가 아닙니다.** ``done`` 만 보던 시절에는
    그것으로 부족했습니다: 계약이 시트에서 들어왔거나(``sheet_to_db``) 운영자가 계약에
    티켓을 손으로 적은 건은 대기를 거친 적이 없어 ``done`` 행 자체가 없고, 그래서 백필과
    10분 스윕이 그 티켓을 훑을 때마다 **이미 등록된 고객이** 「계약 정보를 입력해야
    합니다」 카드로 돌아왔습니다. 티켓은 계약에 붙는 값이므로(``client_contracts.ticket_id``)
    거기 있으면 여기 있을 이유가 없습니다.
    """
    from ..db.models import ClientContract, PendingWon

    ticket_id = str(conv.hubspot_ticket_id or "").strip()
    if not ticket_id:
        return False
    contact = session.get(Contact, conv.contact_id) if conv.contact_id else None
    from .client_ids import find_existing_client_id

    registered = (
        session.query(ClientContract).filter(ClientContract.ticket_id == ticket_id).first()
    )
    # 등록된 계약이 있으면 그 계약의 고객이 이깁니다 — 회사명·도메인 추측보다 확실합니다.
    resolved_client_id = (
        registered.client_id
        if registered is not None
        else sheet_client_id or find_existing_client_id(session, contact)
    )
    changed = False
    if resolved_client_id and conv.sheet_client_id is None:
        conv.sheet_client_id = resolved_client_id
        changed = True
    if resolved_client_id and contact is not None and contact.sheet_client_id is None:
        contact.sheet_client_id = resolved_client_id
        changed = True

    existing = (
        session.query(PendingWon).filter(PendingWon.ticket_id == ticket_id).one_or_none()
    )
    if existing is not None:
        if registered is not None:
            # 계약이 이미 있습니다. 내려놓고 그 고객에 묶습니다.
            if existing.status != "done":
                existing.client_id = registered.client_id
                existing.status = "done"
                changed = True
        elif existing.status == "dismissed":
            # Won 에서 벗어났다가 **돌아왔습니다.** `done`(계약을 받았다)은 되살리지
            # 않습니다 — 그건 이 분기 위에서 이미 걸러집니다.
            existing.status = "pending"
            if resolved_client_id and existing.client_id is None:
                existing.client_id = resolved_client_id
            changed = True
        elif (
            existing.status == "pending"
            and existing.client_id is None
            and resolved_client_id is not None
        ):
            existing.client_id = resolved_client_id
            changed = True
        return changed
    if registered is not None:
        return changed
    session.add(
        PendingWon(
            ticket_id=ticket_id,
            company=(contact.company if contact else None) or (contact.full_name if contact else None),
            client_id=resolved_client_id,
            conversation_id=conv.id,
            # 수주 유형(MRR/PoC)은 담당자가 고릅니다. HubSpot 의 Won type 은 읽지
            # 않습니다 — 그 속성이 이 파이프라인에 있는지 확인되지 않았고, 없는 값을
            # 추측해 채우면 PoC 였던 건이 MRR 로 굳습니다.
            won_type=None,
            won_on=datetime.now(timezone.utc).date().isoformat(),
        )
    )
    logger.info(
        "수주 전환 대기에 추가: ticket=%s client=%s", ticket_id, resolved_client_id
    )
    return True
