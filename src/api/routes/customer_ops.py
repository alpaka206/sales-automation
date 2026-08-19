"""Customer history, pipeline, manual touchpoints, contracts, and sales insights."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from ...agents.stage_sync import _retire_superseded_drafts, customer_state_for
from ...common.config import settings
from ...common.subjects import strip_reply_prefixes
from ...db.models import (
    Client,
    Contact,
    ContractRecord,
    Conversation,
    ConversationProgress,
    CustomerInteraction,
    CustomerProfile,
    Message,
)
from ...db.session import SessionLocal

logger = logging.getLogger(__name__)
router = APIRouter(tags=["web"])
GOOGLE_SHEETS_STATE_COOKIE = "perso_sheets_oauth_state"

# Mirrors the [B2B] AI Dubbing ticket pipeline in HubSpot, in flow order. Operators
# also move tickets in HubSpot directly, so every HubSpot stage needs a local key here
# or src/agents/stage_sync.py cannot record where a ticket actually is.
# Legacy keys (follow_up_needed, contracted, onboarding, active) were retired in
# migration 0040; nothing may reintroduce them without a HubSpot stage to match.
#
# **이 튜플의 두 번째 칸이 파이프라인 이름의 유일한 출처입니다.** HubSpot 이 단계 이름을
# 바꾸면 여기만 바꿉니다 — stage id 도 로컬 키도 그대로이고, 화면·목록·칩이 전부 여기를
# 읽습니다. 그래서 `meeting_link_sent` 는 Qualified 로, `closed` 는 Concluded 로 보입니다:
# 이름만 바뀐 같은 단계라 예전 티켓이 저절로 새 이름 아래로 모입니다(옮길 것이 없습니다).
# `closed` 의 이름 내력: Unqualified → Closed → Not a Fit → Concluded (2026-08-19). 그동안
# stage id 는 1404814097 한 번도 안 바뀌었습니다.
PIPELINE_STAGES: tuple[tuple[str, str, str], ...] = (
    ("new", "New", "새 문의"),
    ("meeting_link_sent", "Qualified", "답변 발송"),
    ("negotiation", "Negotiating", "협의 중"),
    ("reminder_sent", "Reminder Sent", "리마인더 발송"),
    ("won", "Won", "계약 성사"),
    ("closed_lost", "Lost", "실패"),
    # No Response 가 없어지면서 이 단계가 「끝난 문의」 전부를 받습니다(이관 0076) —
    # 이름이 Not a Fit 에서 Concluded 로 넓어진 것도 그래서입니다.
    ("closed", "Concluded", "종결"),
)
VALID_PIPELINE_STAGES = {stage for stage, _, _ in PIPELINE_STAGES}

# ----- Deal Detail — Won 과 Lost 에만 있는 세부 구분 -----
# 두 단계뿐인 이유가 곧 정의입니다: 왜 이겼나(Won Type)와 왜 졌나(Lost Reason)는 결말이
# 난 건에만 있는 정보이고, 나머지 단계에는 채울 답이 없습니다. 그래서 보드 카드의 고르개도
# 이 두 열에서만 그려집니다 — 나머지 열에 두면 아무도 안 고르는 빈 칸이 됩니다.
WON_TYPES: tuple[str, ...] = ("PoC", "Contract", "Renewal")
LOST_REASONS: tuple[str, ...] = (
    "Price",
    # "Use other plan" 은 지웠습니다 (2026-08-19, 운영자 지시). 이미 고른 건이 있으면 그
    # 값은 DB 에 남지만 화면에는 안 나옵니다 — `visible_deal_detail` 이 지금 단계의 목록에
    # 있는 값만 내려보냅니다. 실측으로는 그 값을 쓴 문의가 없었습니다.
    "Competitor",
    "Product Gap",
    "No decision",
    "Went dark",
)
DEAL_DETAILS: dict[str, tuple[str, ...]] = {"won": WON_TYPES, "closed_lost": LOST_REASONS}


def visible_deal_detail(stage: str | None, value: str | None) -> str | None:
    """화면에 내려보낼 Deal Detail — **지금 단계의 목록에 있는 값일 때만.**

    Won 에서 고른 "Contract" 를 그 카드가 Lost 로 옮겨진 뒤에도 그리면, Lost 사유 자리에
    Won 값이 붙은 카드가 됩니다. 값은 지우지 않으므로 되돌아오면 다시 뜹니다.

    보드 카드와 티켓 세부 내역이 같은 값을 보여야 해서 여기 한 곳에 둡니다 — 두 화면이
    각자 판단하면, 한쪽만 고쳤을 때 같은 문의가 두 자리에서 다르게 보입니다.
    """
    return value if value and value in DEAL_DETAILS.get(stage or "", ()) else None
CONTRACT_STATUSES = {"draft", "sent", "contracted", "active", "expired", "cancelled"}
# In the order a contract moves through them, with the words the 수주 고객 screen shows.
CONTRACT_STATUS_LABELS: tuple[tuple[str, str], ...] = (
    ("draft", "작성 중"),
    ("sent", "발송"),
    ("contracted", "계약 체결"),
    ("active", "서비스 중"),
    ("expired", "만료"),
    ("cancelled", "해지"),
)
# What "곧 만료" means on the 수주 고객 and 전체 대시보드 screens. One number, because two
# screens showing different renewal windows is how a renewal gets missed on the screen
# that happened to use the longer one.
RENEWAL_WINDOW_DAYS = 60

# Stages where the automated part of the thread is over. Up to 답변 발송 the app owns the
# conversation (auto-acknowledgement, then the reviewed AI reply out through HubSpot);
# from that point the customer answers on whatever channel they prefer — email, WhatsApp,
# phone, SMS — and only the operator knows what was said. So the board offers its 기록
# 추가 (+) button on these stages and not on 새 문의, where nothing has been answered yet.
_STAGE_ORDER = [stage for stage, _, _ in PIPELINE_STAGES]
MANUAL_LOG_STAGES: tuple[str, ...] = tuple(_STAGE_ORDER[_STAGE_ORDER.index("meeting_link_sent") :])

# How many cards one board column renders **before 더보기**. A column is a fixed-height
# scroller and the busiest stage here holds 202 threads: nobody drags card 150, and
# loading them cost a full read of every conversation, contact and profile on every
# dashboard request. The header keeps showing the REAL total (see _pipeline_rows), and
# the column says so when it is showing fewer than it counts.
#
# 60 → 15 (2026-08-19, 운영자 지시). 첫 화면에 60장이 깔리면 스크롤을 한참 내려야 다음
# 열의 아래쪽이 보이고, 정작 손이 가는 카드는 맨 위 몇 장입니다. 나머지는 열 바닥의
# 「더보기」가 같은 수만큼씩 이어 붙입니다(`/api/ui/pipeline/{stage}/cards?offset=`).
BOARD_CARDS_PER_STAGE = 15

# Logging a 미팅 means the deal is live, and it used to move the thread to 협의 중 from
# WHEREVER it was. That was harmless while the only way to log one was the customer page;
# with a + button on every board card past 답변 발송 it would drag a Won card backwards on
# the next call note. A meeting only ever advances a thread that has not started
# negotiating yet.
_MEETING_ADVANCES_FROM = {"new", "meeting_link_sent"}

# Days of customer silence (measured from our last outgoing mail) at which each rung
# of the B2B follow-up ladder becomes due: reply -> +3d 1st reminder -> +7d 2nd reminder
# -> +3d Unqualified. The reminder MAIL itself is sent by the HubSpot workflow, not by
# this app; these thresholds only drive the read-only /operations board so an operator
# can see which threads HubSpot is about to act on (and catch ones it missed, e.g. a
# deal that moved to another channel and was never pulled into Negotiating).
FOLLOW_UP_REMINDER_1_DAYS = 3
FOLLOW_UP_REMINDER_2_DAYS = FOLLOW_UP_REMINDER_1_DAYS + 7   # 10
FOLLOW_UP_UNQUALIFIED_DAYS = FOLLOW_UP_REMINDER_2_DAYS + 3  # 13


def _announce(topic: str) -> None:
    """Tell every open console something changed.

    Nothing in this module calls it any more — publish_changes_middleware broadcasts
    every successful write, so a handler cannot forget. Kept for a caller that is not an
    HTTP request (a background worker finishing a draft), which the middleware cannot
    see. Best effort: a write must never fail because nobody was listening.
    """
    try:
        from .ui_api import publish

        publish(topic)
    except Exception:  # pragma: no cover - broadcasting is not the operation
        logger.debug("Live update broadcast failed for %s", topic, exc_info=True)


def _stage_id(stage: str) -> str:
    """Local stage key -> HubSpot stage id. Inverse of stage_sync.local_stage_for()."""
    from ...agents.stage_sync import LOCAL_STAGE_TO_SETTING

    attr = LOCAL_STAGE_TO_SETTING.get(stage)
    return (getattr(settings, attr, "") or "").strip() if attr else ""


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def _contract_amount(value: str) -> Decimal | None:
    raw = value.replace(",", "").strip()
    if not raw:
        return None
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail="계약 금액은 숫자로 입력해 주세요.") from exc
    if not amount.is_finite():
        raise HTTPException(status_code=400, detail="계약 금액은 유한한 숫자여야 합니다.")
    return amount


def _contract_conversation(session, contact_id: int, raw_id: str) -> Conversation | None:
    """Resolve an explicit inquiry, otherwise use this contact's latest inquiry."""
    if raw_id.strip():
        try:
            conversation_id = int(raw_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="올바른 문의를 선택해 주세요.") from exc
        conversation = session.get(Conversation, conversation_id)
        if not conversation or conversation.contact_id != contact_id:
            raise HTTPException(status_code=400, detail="이 고객의 문의만 계약에 연결할 수 있습니다.")
        return conversation
    return session.scalar(
        select(Conversation)
        .where(Conversation.contact_id == contact_id)
        .order_by(Conversation.created_at.desc(), Conversation.id.desc())
        .limit(1)
    )


def _set_local_stage(contact_id: int, stage: str) -> tuple[str | None, int | None]:
    """Persist the operator's stage and return its HubSpot and Sheets references."""
    if stage not in VALID_PIPELINE_STAGES:
        raise HTTPException(status_code=400, detail="지원하지 않는 파이프라인 단계입니다")
    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        profile = session.get(CustomerProfile, contact_id) or CustomerProfile(contact_id=contact_id)
        profile.pipeline_stage = stage
        profile.customer_state = customer_state_for(stage, profile.customer_state)
        session.add(profile)
        latest_conversation = session.execute(
            select(Conversation)
            .where(
                Conversation.contact_id == contact_id,
            )
            .order_by(Conversation.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest_conversation:
            latest_conversation.stage = stage
            # 단계를 옮기는 순간 대기 중이던 초안은 늦은 답이 됩니다. HubSpot 에서 옮겼을
            # 때와 같은 처리를, 콘솔에서 옮겼을 때도 합니다.
            _retire_superseded_drafts(session, latest_conversation.id, stage)
        # Capture primitives while the session is open — expire_on_commit=True in
        # production detaches these instances after the `with` block, so reading
        # them in the return tuple would raise DetachedInstanceError.
        ticket_id = latest_conversation.hubspot_ticket_id if latest_conversation else None
        sheet_client_id = (
            latest_conversation.sheet_client_id
            if latest_conversation and latest_conversation.sheet_client_id
            else contact.sheet_client_id
        )
        session.commit()
    return ticket_id, sheet_client_id


async def _sync_stage(
    ticket_id: str | None, stage: str, contact_id: int, sheet_client_id: int | None = None
) -> dict[str, bool | None]:
    """Push a stage the operator just moved to HubSpot and the sales workbook.

    Three-valued per channel, and the distinction is the point: True written, False
    ATTEMPTED AND FAILED, None NOT ATTEMPTED. Not attempted covers a thread with no
    ticket id, an inquiry with no row in the workbook, and pre-launch safe mode — none of
    which is a failure the operator should be warned about. Collapsing "blocked" into
    False made every single card move report 동기화 실패 while the 대전제 is engaged.
    """
    from ...common.safe_mode import ExternalWriteBlocked, live_sheets_writes
    from ...integrations.google_sheets import is_configured, update_inbound_stage

    with SessionLocal() as session:
        profile = session.get(CustomerProfile, contact_id)
        qualification = profile.qualification if profile else None
    sheet_result: bool | None = None
    if sheet_client_id and is_configured():
        sheet_result = (
            await asyncio.to_thread(update_inbound_stage, sheet_client_id, stage, qualification)
            if live_sheets_writes()
            else None
        )

    stage_id = _stage_id(stage)
    if not ticket_id or not stage_id:
        return {"sheets": sheet_result, "hubspot": None}
    from ...integrations.hubspot import HubSpotClient, HubSpotNotConfigured

    try:
        client = HubSpotClient()
        await asyncio.to_thread(client.update_ticket_stage_sync, ticket_id, stage_id)
        hubspot_result: bool | None = True
    except ExternalWriteBlocked:
        # The guard doing its job, not a failure. Must precede the generic handler
        # below — ExternalWriteBlocked is a RuntimeError.
        hubspot_result = None
    except HubSpotNotConfigured:
        hubspot_result = False
    except Exception:
        hubspot_result = False
        logger.warning("HubSpot pipeline sync failed for contact %d", contact_id, exc_info=True)
    return {"sheets": sheet_result, "hubspot": hubspot_result}


def _sync_state(result: dict[str, bool | None]) -> str:
    """The banner flag for what actually happened: partial / ok / local.

    ``local`` is the honest answer when nothing was even attempted — the stage moved in
    this database and nowhere else. It used to render as 동기화 완료, which read as a
    promise that HubSpot and the workbook had been updated.
    """
    if False in result.values():
        return "partial"
    if any(value is True for value in result.values()):
        return "ok"
    return "local"


def _parse_dt(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"날짜 형식이 올바르지 않습니다: {value}") from exc


def _customer_rows() -> list[dict]:
    """One row per contact, newest activity first.

    한 번의 grouped/joined 읽기입니다. 예전에는 대화 전체와 계약 전체를 파이썬으로 끌어와
    세고 고르기만 했는데, 그건 GROUP BY 와 WHERE 가 할 일입니다. 집계는 대화마다가 아니라
    연락처마다 한 행 — 곧 이 페이지의 크기 — 을 돌려줍니다.

    계약 읽기(`status == "active"` 인 ContractRecord 를 연락처별로 하나씩)가 여기 하나 더
    있었습니다. 그 값을 담은 `active_contract` 키를 읽는 곳이 하나도 없어서 지웠습니다 —
    리드 히스토리와 고객 인사이트가 매 요청마다 계약 테이블을 훑고 그 결과를 버렸습니다.
    계약이 필요한 곳은 고객 상세와 갱신 목록이고, 둘 다 자기 조회를 따로 합니다.
    """
    activity = (
        select(
            Conversation.contact_id,
            func.count().label("conversations"),
            func.max(Conversation.last_incoming_at).label("incoming"),
            func.max(Conversation.last_outgoing_at).label("outgoing"),
            func.max(Conversation.created_at).label("created"),
            # **Client ID 도 같이.** 수주 DB·워크북·시트가 전부 이 번호로 엮여 있어서,
            # 리드 히스토리에서 그 번호가 안 보이면 같은 고객을 다른 화면에서 찾을 때
            # 회사 이름으로 눈대중해야 합니다(운영자 지시). 문의마다 붙는 값이라 한
            # 사람에게 여럿일 수 있고, 그때는 **가장 큰 = 가장 최근에 받은** 번호를
            # 목록에 씁니다. 문의별 번호는 상세 화면이 티켓마다 그대로 보여 줍니다.
            func.max(Conversation.sheet_client_id).label("client_id"),
        )
        .group_by(Conversation.contact_id)
        .subquery()
    )
    with SessionLocal() as session:
        loaded = session.execute(
            select(Contact, CustomerProfile, activity)
            .outerjoin(CustomerProfile, CustomerProfile.contact_id == Contact.id)
            .outerjoin(activity, activity.c.contact_id == Contact.id)
        ).all()

    rows: list[dict] = []
    for contact, profile, _cid, conversations, incoming, outgoing, created, client_id in loaded:
        # The LATEST of everything that happened, not the first non-null. `incoming or
        # outgoing` returned the customer's last message even when our reply came after
        # it, so a thread answered this morning reported the inquiry's date and sorted
        # below threads nobody had touched in days — under a column headed 최근 활동.
        # ponytail: the three MAXes are reduced here because "greatest of N columns" has
        # no portable SQL spelling (SQLite max(), PostgreSQL GREATEST). Push it into SQL
        # with a dialect switch only if this list ever needs SQL-side paging.
        stamps = [when for when in (incoming, outgoing, created) if when is not None]
        last_activity = max(stamps) if stamps else contact.updated_at
        rows.append(
            {
                "contact": contact,
                "profile": profile,
                "state": profile.customer_state if profile else "negotiation",
                "stage": profile.pipeline_stage if profile else "new",
                "temperature": profile.lead_temperature if profile else None,
                "next_action": profile.next_action if profile else None,
                "next_action_at": profile.next_action_at if profile else None,
                "last_activity": last_activity,
                "conversation_count": conversations or 0,
                # 연락처에 박힌 값이 먼저입니다 — 문의가 하나도 안 남은 사람도(옛 티켓이
                # 정리된 경우) 번호는 그대로 갖고 있습니다.
                "client_id": contact.sheet_client_id or client_id,
            }
        )
    rows.sort(key=lambda row: row["last_activity"] or datetime.min, reverse=True)
    return rows


_CARD_ORDER = (Conversation.created_at.desc(), Conversation.id.desc())


def _pipeline_rows(
    *,
    stage: str | None = None,
    limit: int = BOARD_CARDS_PER_STAGE,
    offset: int = 0,
) -> tuple[list[dict], dict[str, int]]:
    """Board cards, newest first — one page of them — plus the true size of each column.

    Returns ``(rows, totals)``. ``totals`` is what the column header shows: paging the
    cards must not quietly change the number an operator reads off the board.

    This used to load EVERY conversation, then every contact, then every profile, then a
    grouped scan of the whole messages table — four unbounded reads on every dashboard
    request, to render columns nobody scrolls to the bottom of. Cost is now set by
    ``limit``, not by how much history exists.

    Two shapes, one body. ``stage=None`` is the board's first paint: a window function
    takes the top ``limit`` of EVERY column in one query. ``stage="won"`` is what the
    column asks for when it is scrolled to the bottom, and needs no window at all — a
    filtered LIMIT/OFFSET. Both then join Contact in the same trip and look up
    newest-message ids only for the rows that survived.

    CustomerProfile 도 같이 조인했습니다. 카드가 그 프로필에서 읽는 값이 리드 온도 하나뿐
    이었고 화면은 그것을 그리지 않아서, 대시보드를 그릴 때마다 아무도 안 보는 조인이
    따라왔습니다. 리드 온도가 보이는 곳(리드 히스토리·고객 인사이트)은 `_customer_rows`
    의 자기 조인을 씁니다.

    The window function needs SQLite >= 3.25 (2018) and any supported PostgreSQL.
    """
    query = (
        select(Conversation, Contact)
        .join(Contact, Conversation.contact_id == Contact.id)
        .order_by(*_CARD_ORDER)
    )
    if stage is None:
        numbered = select(
            Conversation.id.label("conversation_id"),
            func.row_number()
            .over(partition_by=Conversation.stage, order_by=_CARD_ORDER)
            .label("rank"),
        ).subquery()
        query = query.join(numbered, numbered.c.conversation_id == Conversation.id).where(
            numbered.c.rank <= limit
        )
    else:
        query = query.where(Conversation.stage == stage).limit(limit).offset(offset)

    with SessionLocal() as session:
        totals: dict[str, int] = {}
        counts = select(Conversation.stage, func.count()).group_by(Conversation.stage)
        if stage is not None:
            counts = counts.where(Conversation.stage == stage)
        for stage_key, count in session.execute(counts).all():
            key = stage_key if stage_key in VALID_PIPELINE_STAGES else "new"
            totals[key] = totals.get(key, 0) + count

        loaded = session.execute(query).all()

        # Only for the cards actually rendered. The old grouped scan read every row in
        # the messages table to answer a question about at most a few hundred threads.
        conversation_ids = [conversation.id for conversation, _c in loaded]
        latest_message = (
            dict(
                session.execute(
                    select(Message.conversation_id, func.max(Message.id))
                    .where(Message.conversation_id.in_(conversation_ids))
                    .group_by(Message.conversation_id)
                ).all()
            )
            if conversation_ids
            else {}
        )
        # Its subject, for the cards whose conversation has no ticket name of its own.
        # Fetched separately rather than selected beside the max(): a bare column next to
        # an aggregate is a SQLite-only liberty, and this has to run on PostgreSQL too.
        subjects = (
            dict(
                session.execute(
                    select(Message.id, Message.subject).where(
                        Message.id.in_(list(latest_message.values()))
                    )
                ).all()
            )
            if latest_message
            else {}
        )

    rows: list[dict] = []
    for conversation, contact in loaded:
        # Conversation.stage is the pipeline source of truth — a card is placed by its
        # own thread's stage, never by the contact's customer-summary stage.
        stage = conversation.stage if conversation.stage in VALID_PIPELINE_STAGES else "new"
        rows.append(
            {
                "conversation": conversation,
                "contact": contact,
                "stage": stage,
                # The workbook's stable key for this inquiry. Threads imported from the
                # sheet carry it on the contact, ones this app appended on the
                # conversation — same fallback order as every stage-sync path.
                "client_id": conversation.sheet_client_id or contact.sheet_client_id,
                # None only for a thread with no message rows at all (a backfilled
                # ticket whose mail was never ingested); the card falls back to the
                # customer page then.
                "link_message_id": latest_message.get(conversation.id),
                # 카드 제목. 회신 및 검토 목록과 같은 식으로 고릅니다 — 티켓 이름이 없으면
                # 그 티켓의 마지막 메일 제목에서 우리가 붙인 "RE:" 를 떼어 씁니다. 두 화면이
                # 같은 티켓을 다른 이름으로 부르면 같은 건인 줄 모릅니다. 시트에서 들여온
                # 문의는 메일이 없어서 여기서도 비고, 그때만 "(제목 없음)" 이 뜹니다.
                "subject": conversation.inquiry_subject
                or strip_reply_prefixes(subjects.get(latest_message.get(conversation.id)))
                or None,
                # Latest of the three, for the same reason as _customer_rows: `incoming
                # or outgoing` dated a card by the customer's message even when our reply
                # was newer.
                "last_activity": max(
                    when
                    for when in (
                        conversation.last_incoming_at,
                        conversation.last_outgoing_at,
                        conversation.created_at,
                    )
                    if when is not None
                ),
            }
        )
    return rows, totals


def _set_conversation_stage(conversation_id: int, stage: str) -> tuple[str | None, int, int | None]:
    if stage not in VALID_PIPELINE_STAGES:
        raise HTTPException(status_code=400, detail="지원하지 않는 파이프라인 단계입니다")
    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다")
        contact = session.get(Contact, conversation.contact_id)
        if not contact:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        conversation.stage = stage
        _retire_superseded_drafts(session, conversation.id, stage)
        latest_id = session.scalar(
            select(Conversation.id)
            .where(Conversation.contact_id == contact.id)
            .order_by(Conversation.created_at.desc(), Conversation.id.desc())
            .limit(1)
        )
        if latest_id == conversation.id:
            profile = session.get(CustomerProfile, contact.id) or CustomerProfile(
                contact_id=contact.id
            )
            profile.pipeline_stage = stage
            profile.customer_state = customer_state_for(stage, profile.customer_state)
            session.add(profile)
        # Fall back to the CONTACT's sheet id, exactly as _set_local_stage and the send
        # worker do. A conversation gets its own id only when this app appended the row;
        # rows imported from the workbook carry it on the contact, and without this
        # fallback a board drop for one of those silently skipped the Sheet.
        sheet_client_id = conversation.sheet_client_id or contact.sheet_client_id
        session.commit()
        return (
            conversation.hubspot_ticket_id,
            contact.id,
            sheet_client_id,
        )


def _customer_context(contact_id: int) -> dict | None:
    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            return None
        profile = session.get(CustomerProfile, contact_id)
        conversations = (
            session.execute(
                select(Conversation)
                .where(Conversation.contact_id == contact_id)
                .order_by(Conversation.created_at.desc())
            )
            .scalars()
            .all()
        )
        conv_ids = [c.id for c in conversations]
        messages = (
            session.execute(
                select(Message)
                .where(Message.conversation_id.in_(conv_ids))
                .order_by(Message.created_at.desc())
            )
            .scalars()
            .all()
            if conv_ids
            else []
        )
        interactions = (
            session.execute(
                select(CustomerInteraction)
                .where(CustomerInteraction.contact_id == contact_id)
                .order_by(CustomerInteraction.happened_at.desc())
            )
            .scalars()
            .all()
        )
        contracts = (
            session.execute(
                select(ContractRecord)
                .where(ContractRecord.contact_id == contact_id)
                .order_by(ContractRecord.created_at.desc())
            )
            .scalars()
            .all()
        )
        same_company = []
        if contact.domain:
            same_company = (
                session.execute(
                    select(Contact)
                    .where(Contact.domain == contact.domain, Contact.id != contact.id)
                    .order_by(Contact.full_name)
                )
                .scalars()
                .all()
            )

        # **티켓 단위로 묶습니다.** 예전에는 이 화면이 그 사람의 모든 메일을 한 덩어리로
        # 섞어 시간순으로만 보여 줬습니다. 문의가 둘 이상인 고객에서는 어느 메일이 어느
        # 건인지 알 수 없었고, 타임라인 항목에 `conversation_id` 조차 안 담겨서 화면이
        # 나누고 싶어도 재료가 없었습니다 (2026-08-19 운영자 지시).
        progress_rows = (
            session.execute(
                select(ConversationProgress)
                .where(ConversationProgress.conversation_id.in_(conv_ids))
                .order_by(ConversationProgress.created_at)
            )
            .scalars()
            .all()
            if conv_ids
            else []
        )
        by_conversation: dict[int, list] = {cid: [] for cid in conv_ids}
        for message in messages:
            by_conversation.setdefault(message.conversation_id, []).append(message)
        progress_by_conversation: dict[int, list] = {cid: [] for cid in conv_ids}
        for row in progress_rows:
            progress_by_conversation.setdefault(row.conversation_id, []).append(row)

        tickets = [
            {
                "conversation": conversation,
                # 오래된 것부터. 티켓 안에서는 대화 순서가 곧 읽는 순서입니다 — 목록 전체는
                # 최신 티켓이 위지만, 한 티켓 안에서 답장이 문의보다 위에 있으면 안 됩니다.
                "messages": sorted(
                    by_conversation.get(conversation.id, []),
                    key=lambda m: m.sent_at or m.created_at or datetime.min,
                ),
                "progress": progress_by_conversation.get(conversation.id, []),
            }
            for conversation in conversations
        ]

        # 수주 고객. 정식 연결(`clients.contact_id`)이 먼저지만 운영 DB 에서 그 값이 거의
        # 비어 있어(고객 추가 폼이 안 받습니다) Client ID 로도 찾습니다 — 문의와 수주 고객이
        # 같은 번호대를 씁니다(`conversations.sheet_client_id` ↔ `clients.client_id`).
        client_ids = {
            cid
            for cid in [contact.sheet_client_id, *(c.sheet_client_id for c in conversations)]
            if cid
        }
        won_client = session.execute(
            select(Client).where(Client.contact_id == contact.id)
        ).scalars().first()
        if won_client is None and client_ids:
            won_client = session.execute(
                select(Client).where(Client.client_id.in_(client_ids))
            ).scalars().first()

        timeline = [
            {
                "channel": m.channel,
                "direction": m.direction,
                "handler": None,
                "subject": m.subject,
                "summary": m.body,
                "context": None,
                "artifact_url": None,
                "happened_at": m.sent_at or m.created_at,
                "source": "message",
            }
            for m in messages
        ]
        timeline.extend(
            {
                "channel": item.channel,
                "direction": item.direction,
                "handler": item.handler,
                "subject": item.subject,
                "summary": item.summary,
                "context": item.context,
                "artifact_url": item.artifact_url,
                "happened_at": item.happened_at,
                "source": "interaction",
            }
            for item in interactions
        )
        timeline.sort(key=lambda item: item["happened_at"] or datetime.min, reverse=True)

        return {
            "contact": contact,
            "profile": profile,
            "conversations": conversations,
            "tickets": tickets,
            "client_ids": sorted(client_ids),
            "won_client": won_client,
            "won_contracts": list(won_client.contracts) if won_client else [],
            "interactions": interactions,
            "contracts": contracts,
            "timeline": timeline[:100],
            "same_company": same_company,
            # Drives the pipeline <select>. Hardcoding it here once let this page keep
            # offering stages the board had already dropped, which POSTs then rejected.
            "stage_options": PIPELINE_STAGES,
        }


@router.post("/customers/{contact_id}/profile")
async def customer_profile_save(
    contact_id: int,
    pipeline_stage: str = Form("new"),
    lead_temperature: str = Form(""),
    next_action: str = Form(""),
    next_action_at: str = Form(""),
    industry: str = Form(""),
    user_seq: str = Form(""),
    current_plan: str = Form(""),
    qualification: str = Form(""),
    lost_reason: str = Form(""),
    source: str = Form(""),
    notes: str = Form(""),
):
    if pipeline_stage not in VALID_PIPELINE_STAGES:
        raise HTTPException(status_code=400, detail="지원하지 않는 파이프라인 단계입니다")
    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        profile = session.get(CustomerProfile, contact_id) or CustomerProfile(contact_id=contact_id)
        session.add(profile)
        profile.pipeline_stage = pipeline_stage
        # 「고객 구분」 칸이 화면에서 없어졌습니다 — 손으로 고르던 값인데, 단계에서 그대로
        # 나오는 값이라 둘이 어긋난 행이 생겼습니다(Won 인데 Negotiation 인 고객). 이제
        # 보드 드롭·HubSpot 동기화·워크북이 쓰는 것과 **같은 규칙**으로 여기서도 정합니다.
        profile.customer_state = customer_state_for(pipeline_stage, profile.customer_state)
        profile.lead_temperature = lead_temperature.strip() or None
        profile.next_action = next_action.strip() or None
        profile.next_action_at = _parse_dt(next_action_at)
        profile.industry = industry.strip() or None
        profile.user_seq = user_seq.strip() or None
        profile.current_plan = current_plan.strip() or None
        profile.qualification = qualification.strip() or None
        profile.lost_reason = lost_reason.strip() or None
        profile.source = source.strip() or None
        profile.notes = notes.strip() or None
        # 파이프라인 단계를 옮기는 **세 번째** 폼입니다(보드 카드 둘이 앞의 둘). 여기서도
        # 대기 중이던 초안은 늦은 답이 됩니다. 티켓이 있는 문의는 이 아래 _sync_stage 가
        # HubSpot 을 움직여 폴러가 돌아오지만, 티켓이 없는 문의(연락처만으로 들어온 건)는
        # 돌아올 길이 없어서 여기서 안 닫으면 영영 발송 대기에 남습니다.
        latest = session.scalar(
            select(Conversation)
            .where(Conversation.contact_id == contact_id)
            .order_by(Conversation.created_at.desc())
        )
        if latest:
            # 대화의 단계도 같이 옮깁니다. 예전에는 프로필만 옮겼는데, 그러면 두 열이
            # 어긋난 채로 남고 화면은 자리마다 다른 값을 보여 줍니다(보드는 대화, 리드
            # 히스토리는 프로필). 옮기는 곳이 여기 하나뿐인 것도 아니라 — 발송 워커는
            # 반대로 대화만 옮깁니다 — 한쪽만 쓰는 폼이 하나라도 있으면 어긋남이 계속
            # 생깁니다. 허브스팟 동기화가 이제 둘 다 맞추지만, 애초에 안 어긋나게 합니다.
            latest.stage = pipeline_stage
            _retire_superseded_drafts(session, latest.id, pipeline_stage)
        latest_ticket = (
            session.execute(
                select(Conversation)
                .where(
                    Conversation.contact_id == contact_id,
                    Conversation.hubspot_ticket_id.isnot(None),
                )
                .order_by(Conversation.created_at.desc())
            )
            .scalars()
            .first()
        )
        # Capture primitives before the session closes (expire_on_commit=True in
        # production would detach latest_ticket/contact after the `with` block).
        ticket_id = latest_ticket.hubspot_ticket_id if latest_ticket else None
        sheet_client_id = (
            latest_ticket.sheet_client_id
            if latest_ticket and latest_ticket.sheet_client_id
            else contact.sheet_client_id
        )
        session.commit()

    await _sync_stage(ticket_id, pipeline_stage, contact_id, sheet_client_id)
    return RedirectResponse(f"/customers/{contact_id}", status_code=303)


def _internal_path(target: str, fallback: str) -> str:
    """A same-site absolute path, or the fallback. Never an off-site redirect.

    The return address arrives in a form field, so ``//evil.example`` and
    ``https://evil.example`` — both of which a browser follows off this site — are
    rejected, as are the backslash and CR/LF forms used to smuggle them past a naive
    prefix check.
    """
    value = (target or "").strip()
    ok = (
        value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and "\r" not in value
        and "\n" not in value
    )
    return value if ok else fallback


def _linked_conversation(session, raw: str, contact_id: int) -> Conversation | None:
    """The inquiry a manual record belongs to, or None for a contact-wide note.

    The id comes from a hidden form field, so it is checked against THIS contact —
    otherwise an edited field could file one customer's call notes under another
    customer's ticket.
    """
    value = (raw or "").strip()
    if not value.isdigit():
        return None
    conversation = session.get(Conversation, int(value))
    if conversation is None or conversation.contact_id != contact_id:
        return None
    return conversation


# The console's channel keys spelled for a person reading HubSpot. The console's own
# copy is frontend/src/ui/InteractionForm.tsx — this one exists because a note saying
# "manual" or "kakao" tells the reader nothing, and the two drifting apart costs one
# slightly different word in a note, which is cheaper than a round trip to fetch labels.
_CHANNEL_LABELS = {
    "email": "이메일", "whatsapp": "WhatsApp", "phone": "전화", "sms": "문자",
    "kakao": "카카오톡", "meeting": "미팅", "hubspot": "HubSpot",
    "invoice": "Invoice", "contract": "계약", "manual": "메모",
}


async def _log_interaction_to_hubspot(
    hubspot_contact_id: str | None,
    hubspot_ticket_id: str | None,
    *,
    channel: str,
    handler: str,
    subject: str,
    summary: str,
    happened_at: datetime,
) -> None:
    """Copy one 소통 히스토리 onto the HubSpot timeline. Best effort, and deliberately so.

    The record is committed before this runs and this console stays the place the
    post-발송 history lives. The copy is for whoever opens the contact in HubSpot: without
    it a customer we have been talking to for a month reads there as one nobody has
    touched since the first reply. So a HubSpot failure is a log line — a CRM copy must
    never be able to lose the record it is a copy of.

    Nothing is written for a contact HubSpot does not have (sheet-imported ones): there
    is no timeline to write to, and inventing a contact to hold a note is not this
    function's call to make.
    """
    if not hubspot_contact_id:
        return
    head = f"[{_CHANNEL_LABELS.get(channel, channel)}]"
    if subject:
        head = f"{head} {subject}"
    if handler:
        head = f"{head} · 담당 {handler}"
    try:
        from ...integrations.hubspot import HubSpotClient

        await HubSpotClient().create_interaction_note(
            hubspot_contact_id,
            f"{head}\n\n{summary}",
            happened_at=happened_at,
            ticket_id=hubspot_ticket_id,
        )
    except Exception:
        logger.warning(
            "소통 히스토리는 저장됐지만 HubSpot 타임라인에 남기지 못했습니다 (contact %s).",
            hubspot_contact_id,
            exc_info=True,
        )


@router.post("/customers/{contact_id}/interactions")
async def interaction_add(
    contact_id: int,
    channel: str = Form("manual"),
    direction: str = Form("note"),
    handler: str = Form(""),
    subject: str = Form(""),
    summary: str = Form(""),
    context: str = Form(""),
    artifact_url: str = Form(""),
    happened_at: str = Form(""),
    conversation_id: str = Form(""),
    contract_seq: str = Form(""),
    redirect_to: str = Form(""),
):
    """Record one manual touchpoint — email, WhatsApp, phone, SMS, meeting.

    This is the only way anything that happened after the first reply reaches this
    system: from 답변 발송 onward the thread leaves HubSpot and only the operator knows
    what was said.

    One record is the whole exchange, summarized once. The form used to ask for a
    ``direction`` and that was the wrong question — it made the operator cut one
    conversation into "who spoke" rows. It asks who handled it instead; ``direction``
    keeps its default here and carries a real value only on HubSpot-synced rows.

    ``conversation_id`` files the record under ONE inquiry (the board's + button and the
    ticket screen send it, so the ticket can show its own log); the contact-level form on
    리드 히스토리 leaves it blank and the record stays customer-wide. ``redirect_to``
    returns the operator to where they were instead of the customer page.
    """
    if not summary.strip():
        return HTMLResponse("내용을 입력해 주세요.", status_code=400)
    back = _internal_path(redirect_to, f"/customers/{contact_id}#history")
    # Which thread (if any) the 미팅 rule below should advance. Decided inside the
    # session, applied after the commit — _set_* open their own sessions.
    advance_conversation_id: int | None = None
    advance_contact = False
    # Read inside the session, used after it closes — see _log_interaction_to_hubspot.
    hubspot_contact_id: str | None = None
    hubspot_ticket_id: str | None = None
    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        hubspot_contact_id = contact.hubspot_contact_id
        conversation = _linked_conversation(session, conversation_id, contact_id)
        hubspot_ticket_id = conversation.hubspot_ticket_id if conversation else None
        session.add(
            CustomerInteraction(
                contact_id=contact_id,
                conversation_id=conversation.id if conversation else None,
                channel=channel[:32],
                direction=direction[:16],
                handler=handler.strip()[:120] or None,
                subject=subject.strip()[:300] or None,
                summary=summary.strip(),
                context=context.strip() or None,
                artifact_url=artifact_url.strip() or None,
                happened_at=_parse_dt(happened_at) or datetime.now(timezone.utc),
                # 수주 고객의 몇 차 계약에 대한 기록인지. 비면 협상 단계(계약 전)이고,
                # 이 타임라인은 고객 단위라 계약보다 먼저 시작합니다.
                contract_seq=int(contract_seq) if contract_seq.strip().isdigit() else None,
            )
        )
        if channel == "meeting":
            if conversation is not None:
                # A record filed against one ticket moves THAT ticket, never the
                # contact's newest — the operator clicked a specific card. Unknown
                # stages ("initial", pre-0040 keys) read as 새 문의, exactly as the
                # board renders them.
                current = (
                    conversation.stage
                    if conversation.stage in VALID_PIPELINE_STAGES
                    else "new"
                )
                if current in _MEETING_ADVANCES_FROM:
                    advance_conversation_id = conversation.id
            else:
                profile = session.get(CustomerProfile, contact_id) or CustomerProfile(
                    contact_id=contact_id
                )
                # A profile built a line ago has no stage yet — the column default is
                # applied on INSERT, so read it as the 새 문의 it is about to become.
                if (profile.pipeline_stage or "new") in _MEETING_ADVANCES_FROM:
                    profile.customer_state = "negotiation"
                    profile.pipeline_stage = "negotiation"
                    session.add(profile)
                    advance_contact = True
        session.commit()

    await _log_interaction_to_hubspot(
        hubspot_contact_id,
        hubspot_ticket_id,
        channel=channel,
        handler=handler.strip(),
        subject=subject.strip(),
        summary=summary.strip(),
        happened_at=_parse_dt(happened_at) or datetime.now(timezone.utc),
    )

    if advance_conversation_id is not None:
        ticket_id, _contact_id, sheet_client_id = _set_conversation_stage(
            advance_conversation_id, "negotiation"
        )
        await _sync_stage(ticket_id, "negotiation", contact_id, sheet_client_id)
    elif advance_contact:
        ticket_id, sheet_client_id = _set_local_stage(contact_id, "negotiation")
        await _sync_stage(ticket_id, "negotiation", contact_id, sheet_client_id)
    return RedirectResponse(back, status_code=303)


@router.post("/customers/{contact_id}/contracts")
async def contract_add(
    contact_id: int,
    conversation_id: str = Form(""),
    status: str = Form("draft"),
    plan: str = Form(""),
    amount: str = Form(""),
    currency: str = Form("KRW"),
    payment_method: str = Form(""),
    payment_instrument: str = Form(""),
    payment_terms: str = Form("일시불"),
    contract_method: str = Form(""),
    billing_email: str = Form(""),
    contract_months: str = Form(""),
    owner_email: str = Form(""),
    space_seq: str = Form(""),
    plan_start_date: str = Form(""),
    enterprise_name: str = Form(""),
    invitation_limit: str = Form(""),
    queue_limit: str = Form(""),
    concurrent_jobs: str = Form(""),
    space_count: str = Form(""),
    contract_credits: str = Form(""),
    credit_history: str = Form(""),
    payer: str = Form(""),
    plan_notes: str = Form(""),
    contract_date: str = Form(""),
    payment_due_at: str = Form(""),
    paid_at: str = Form(""),
    expires_at: str = Form(""),
    language_pairs: str = Form(""),
    unit_price: str = Form(""),
    quote_url: str = Form(""),
    invoice_url: str = Form(""),
    payment_url: str = Form(""),
    notes: str = Form(""),
):
    parsed_amount = _contract_amount(amount)
    if status not in CONTRACT_STATUSES:
        raise HTTPException(status_code=400, detail="지원하지 않는 계약 상태입니다")
    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        conversation = _contract_conversation(session, contact_id, conversation_id)
        sheet_fields = {
            "payment_instrument": payment_instrument.strip(),
            "payment_terms": payment_terms.strip(),
            "contract_method": contract_method.strip(),
            "billing_email": billing_email.strip(),
            "contract_months": contract_months.strip(),
            "owner_email": owner_email.strip(),
            "space_seq": space_seq.strip(),
            "plan_start_date": plan_start_date.strip(),
            "enterprise_name": enterprise_name.strip(),
            "invitation_limit": invitation_limit.strip(),
            "queue_limit": queue_limit.strip(),
            "concurrent_jobs": concurrent_jobs.strip(),
            "space_count": space_count.strip(),
            "contract_credits": contract_credits.strip(),
            "credit_history": credit_history.strip(),
            "payer": payer.strip(),
            "plan_notes": plan_notes.strip(),
        }
        contract = ContractRecord(
            contact_id=contact_id,
            conversation_id=conversation.id if conversation else None,
            sheet_client_id=conversation.sheet_client_id if conversation else None,
            status=status[:32],
            plan=plan.strip() or None,
            amount=parsed_amount,
            currency=currency.strip().upper()[:8] or "KRW",
            payment_method=payment_method.strip() or None,
            contract_date=_parse_dt(contract_date),
            payment_due_at=_parse_dt(payment_due_at),
            paid_at=_parse_dt(paid_at),
            expires_at=_parse_dt(expires_at),
            language_pairs=[v.strip() for v in language_pairs.split(",") if v.strip()] or None,
            unit_price=unit_price.strip() or None,
            quote_url=quote_url.strip() or None,
            invoice_url=invoice_url.strip() or None,
            payment_url=payment_url.strip() or None,
            notes=notes.strip() or None,
            sheet_fields={key: value for key, value in sheet_fields.items() if value},
        )
        session.add(contract)
        # A contract status is NOT a pipeline stage. Until migration 0040 this wrote
        # status straight into pipeline_stage ("contracted"/"active"), which is how
        # those strings became board columns in the first place. The board stage stays
        # the operator's call — saving a contract only settles customer_state.
        if status in {"active", "contracted"}:
            profile = session.get(CustomerProfile, contact_id) or CustomerProfile(
                contact_id=contact_id
            )
            profile.customer_state = "service" if status == "active" else "negotiation"
            profile.current_plan = plan.strip() or profile.current_plan
            session.add(profile)
        session.commit()
        contract_id = contract.id

    if status in {"active", "contracted"}:
        from ...agents.sheet_sync import sync_contract_order

        await asyncio.to_thread(sync_contract_order, contract_id)
    return RedirectResponse(f"/customers/{contact_id}#contracts", status_code=303)


@router.post("/customers/{contact_id}/contracts/{contract_id}")
async def contract_update(
    contact_id: int,
    contract_id: int,
    conversation_id: str = Form(""),
    status: str = Form("draft"),
    plan: str = Form(""),
    amount: str = Form(""),
    currency: str = Form("KRW"),
    payment_method: str = Form(""),
    contract_date: str = Form(""),
    payment_due_at: str = Form(""),
    paid_at: str = Form(""),
    expires_at: str = Form(""),
    language_pairs: str = Form(""),
    unit_price: str = Form(""),
    quote_url: str = Form(""),
    invoice_url: str = Form(""),
    payment_url: str = Form(""),
    notes: str = Form(""),
):
    """Correct operator-owned contract facts without creating a duplicate row."""
    parsed_amount = _contract_amount(amount)
    if status not in CONTRACT_STATUSES:
        raise HTTPException(status_code=400, detail="지원하지 않는 계약 상태입니다")

    with SessionLocal() as session:
        contract = session.get(ContractRecord, contract_id)
        contact = session.get(Contact, contact_id)
        if not contact or not contract or contract.contact_id != contact_id:
            raise HTTPException(status_code=404, detail="계약을 찾을 수 없습니다")
        conversation = _contract_conversation(session, contact_id, conversation_id)
        contract.conversation_id = conversation.id if conversation else None
        contract.sheet_client_id = conversation.sheet_client_id if conversation else None
        contract.status = status
        contract.plan = plan.strip() or None
        contract.amount = parsed_amount
        contract.currency = currency.strip().upper()[:8] or "KRW"
        contract.payment_method = payment_method.strip() or None
        contract.contract_date = _parse_dt(contract_date)
        contract.payment_due_at = _parse_dt(payment_due_at)
        contract.paid_at = _parse_dt(paid_at)
        contract.expires_at = _parse_dt(expires_at)
        contract.language_pairs = [v.strip() for v in language_pairs.split(",") if v.strip()] or None
        contract.unit_price = unit_price.strip() or None
        contract.quote_url = quote_url.strip() or None
        contract.invoice_url = invoice_url.strip() or None
        contract.payment_url = payment_url.strip() or None
        contract.notes = notes.strip() or None
        contract.sheet_synced_at = None
        session.commit()

    # As in contract_add: the contract status no longer drives the board stage.
    if status in {"contracted", "active"}:
        from ...agents.sheet_sync import sync_contract_order

        await asyncio.to_thread(sync_contract_order, contract_id)
    return RedirectResponse(f"/customers/{contact_id}#contracts", status_code=303)


def _sync_ticket_stages(client, contact_id: int) -> int:
    """이 고객의 티켓 단계를 HubSpot 에서 다시 읽어 옵니다. 바뀐 건수를 돌려줍니다.

    「HubSpot 동기화」 버튼이 컨택 속성·메일·통화·딜·메모만 가져오고 **티켓 단계는 한 번도
    읽지 않았습니다.** 그래서 HubSpot 에서 Lost 로 옮긴 티켓이 이 화면에서는 예전 단계
    그대로였고, 눌러도 아무 일이 없었습니다 — 10분 폴러가 그 티켓을 다시 훑기 전까지는
    고칠 방법도 없었습니다(폴러는 **최근에 바뀐** 티켓만 봅니다).

    쓰는 곳은 ``stage_sync.sync_stage_from_hubspot`` 하나입니다. 여기서 conv.stage 를 직접
    쓰면 초안 종료·프로필 상태·워크북 반영이 이 경로에서만 빠집니다.
    """
    from ...agents.stage_sync import sync_stage_from_hubspot

    with SessionLocal() as session:
        tickets = [
            str(ticket_id)
            for (ticket_id,) in session.execute(
                select(Conversation.hubspot_ticket_id).where(
                    Conversation.contact_id == contact_id,
                    Conversation.hubspot_ticket_id.isnot(None),
                )
            )
            if ticket_id
        ]
    moved = 0
    for ticket_id in tickets:
        try:
            ticket = client.get_ticket_sync(ticket_id)
        except Exception:
            # 지워졌거나 못 읽은 티켓 하나가 나머지 동기화를 통째로 실패시키면 안 됩니다.
            logger.warning("티켓 %s 단계를 읽지 못했습니다", ticket_id, exc_info=True)
            continue
        if sync_stage_from_hubspot(ticket_id, ticket.pipeline_stage, source="manual-sync"):
            moved += 1
    return moved


def _sync_hubspot(contact_id: int, per_type: int = 20) -> int:
    """허브스팟에 있는 그 사람의 기록을 우리 히스토리로 가져옵니다.

    ``per_type`` 은 종류마다 몇 개까지 훑을지입니다. 화면에서 손으로 누르는 동기화는 20 —
    「최근 것 좀 당겨오기」이고 사람이 기다리는 중이라 왕복이 짧아야 합니다. **과거 이관**은
    깊게 팝니다(`/internal/customers/hubspot-history`): 이 콘솔이 생기기 전의 문의·답변이
    허브스팟에만 있고, 그것이 리드 히스토리를 볼 이유의 절반입니다.

    같은 것을 두 번 넣지 않습니다 — 모든 행이 `external_id`(`hubspot:email:123` 꼴)로
    먼저 조회됩니다. 그래서 몇 번을 돌려도 안전하고, 중간에 끊기면 다시 돌리면 됩니다.
    """
    from ...integrations.hubspot import HubSpotClient

    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact or not contact.hubspot_contact_id:
            raise ValueError("HubSpot 연락처 ID가 없습니다")
        hubspot_id = contact.hubspot_contact_id

    client = HubSpotClient()
    dto = client.get_contact_sync(hubspot_id)
    emails = client.get_recent_emails_sync(hubspot_id, limit=per_type)
    # Calls, meetings and messages somebody logged in HubSpot by hand. Without these the
    # 리드 히스토리 screen — the one that claims to hold everything — silently omits every
    # touchpoint that happened on the other side.
    logged = client.get_logged_engagements_sync(hubspot_id, limit=per_type)
    deals = client.get_associated_deals_sync(hubspot_id)
    note = client.get_latest_note(hubspot_id)
    inserted = 0
    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            return 0
        contact.email = dto.email or contact.email
        contact.full_name = " ".join(filter(None, [dto.firstname, dto.lastname])) or contact.full_name
        contact.company = dto.company or contact.company
        contact.phone = dto.phone or contact.phone
        contact.country = dto.country or contact.country
        contact.lifecycle_stage = dto.lifecyclestage or contact.lifecycle_stage
        profile = session.get(CustomerProfile, contact_id) or CustomerProfile(contact_id=contact_id)
        profile.last_synced_at = datetime.now(timezone.utc)
        session.add(profile)

        for email in emails:
            external_id = f"hubspot:email:{email.id}"
            exists = session.scalar(
                select(CustomerInteraction.id).where(CustomerInteraction.external_id == external_id)
            )
            if not exists:
                session.add(
                    CustomerInteraction(
                        contact_id=contact_id,
                        channel="email",
                        direction="incoming" if "incoming" in email.type else "outgoing",
                        subject=email.subject,
                        summary=email.body or email.subject or "HubSpot 이메일",
                        external_id=external_id,
                        happened_at=email.timestamp or datetime.now(timezone.utc),
                    )
                )
                inserted += 1
        for hubspot_channel, engagement in logged:
            external_id = f"hubspot:{engagement.id}"
            exists = session.scalar(
                select(CustomerInteraction.id).where(CustomerInteraction.external_id == external_id)
            )
            if not exists:
                session.add(
                    CustomerInteraction(
                        contact_id=contact_id,
                        channel=hubspot_channel,
                        # `note` is this table's "no direction" — a logged call says
                        # nothing about who dialled, and the form stopped asking for
                        # the same reason.
                        direction="note",
                        subject=engagement.subject,
                        summary=engagement.body or engagement.subject or "HubSpot 기록",
                        external_id=external_id,
                        happened_at=engagement.timestamp or datetime.now(timezone.utc),
                    )
                )
                inserted += 1
        for deal in deals:
            external_id = f"hubspot:deal:{deal.id}"
            exists = session.scalar(
                select(CustomerInteraction.id).where(CustomerInteraction.external_id == external_id)
            )
            if not exists:
                session.add(
                    CustomerInteraction(
                        contact_id=contact_id,
                        channel="hubspot",
                        direction="note",
                        subject=deal.name or "HubSpot Deal",
                        summary=f"단계: {deal.stage or '-'} · 금액: {deal.amount or '-'}",
                        external_id=external_id,
                    )
                )
                inserted += 1
        if note:
            digest = hashlib.sha256(note.encode("utf-8")).hexdigest()[:16]
            external_id = f"hubspot:note:{digest}"
            exists = session.scalar(
                select(CustomerInteraction.id).where(CustomerInteraction.external_id == external_id)
            )
            if not exists:
                session.add(
                    CustomerInteraction(
                        contact_id=contact_id,
                        channel="hubspot",
                        direction="note",
                        subject="HubSpot 메모",
                        summary=note,
                        external_id=external_id,
                    )
                )
                inserted += 1
        session.commit()
    # 커밋 뒤입니다 — 단계 반영은 자기 세션에서 커밋하고, 실패해도 위에서 가져온 것을
    # 되돌리지 않습니다.
    _sync_ticket_stages(client, contact_id)
    return inserted


@router.post("/internal/customers/hubspot-history")
async def internal_contact_history_backfill(limit: int = 20, per_type: int = 100):
    """이 콘솔이 생기기 전의 기록을 허브스팟에서 끌어옵니다 — **한 번에 조금씩, 여러 번.**

    한 요청이 연락처 ``limit`` 명을 처리하고 남은 수를 돌려줍니다. 다 될 때까지 다시 부르면
    됩니다. 288명을 한 요청에 넣지 않는 이유가 셋입니다: ① 연락처 한 명이 허브스팟 왕복
    수십 번이라 한 번에 다 하면 몇십 분짜리 HTTP 요청이 되고, ② 중간에 끊기면 어디까지
    했는지 알 수 없으며, ③ 레이트 리밋에 걸리면 통째로 실패합니다.

    **어디까지 했는지는 따로 기록하지 않습니다.** `customer_profiles.last_synced_at` 이
    이미 그 값이고(`_sync_hubspot` 이 매번 찍습니다), 오래된 것부터 고르면 그것이 곧
    이어하기입니다. 한 명이 실패해도 나머지는 계속합니다 — 지워진 연락처, 권한 없는
    레코드는 흔합니다.
    """
    from sqlalchemy import func

    try:
        with SessionLocal() as session:
            # **Concluded 만 남은 사람은 건너뜁니다** (2026-08-19 운영자 지시). 전체 321건
            # 중 202건이 그 단계이고, 끝난 문의의 옛 메일을 다 끌어오는 것은 시간도 API
            # 호출도 가장 많이 쓰면서 볼 일은 가장 적습니다. 한 사람에게 Concluded 가
            # 아닌 티켓이 하나라도 있으면 대상입니다 — 그 사람의 히스토리는 통째로
            # 이어져야 하니까요.
            wanted = (
                select(Conversation.contact_id)
                .where(Conversation.stage != "closed")
                .distinct()
                .scalar_subquery()
            )
            candidates = (
                session.execute(
                    select(Contact.id)
                    .outerjoin(CustomerProfile, CustomerProfile.contact_id == Contact.id)
                    .where(
                        Contact.hubspot_contact_id.isnot(None),
                        Contact.hubspot_contact_id != "",
                        Contact.id.in_(wanted),
                    )
                    # 아직 한 번도 안 한 사람 먼저, 그다음 오래된 순.
                    .order_by(
                        CustomerProfile.last_synced_at.is_(None).desc(),
                        CustomerProfile.last_synced_at.asc(),
                        Contact.id.asc(),
                    )
                    .limit(max(1, min(limit, 200)))
                )
                .scalars()
                .all()
            )
            total = session.scalar(
                select(func.count(Contact.id)).where(
                    Contact.hubspot_contact_id.isnot(None),
                    Contact.hubspot_contact_id != "",
                    Contact.id.in_(wanted),
                )
            )
    except Exception as exc:
        # **왜 실패했는지 돌려줍니다.** 핸들러가 던진 예외는 로그 버퍼를 건너뛰므로
        # (`/logs` 는 미들웨어가 본 응답만 적습니다) 500 만 보고는 아무것도 알 수 없습니다.
        logger.warning("과거 기록 이관 대상 조회 실패", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"대상 조회 실패: {type(exc).__name__}: {exc}"
        ) from exc

    inserted = 0
    failed: list[str] = []
    for contact_id in candidates:
        try:
            inserted += await asyncio.to_thread(_sync_hubspot, contact_id, per_type)
        except Exception as exc:
            logger.warning("과거 기록 이관 실패 (contact=%s)", contact_id, exc_info=True)
            failed.append(f"{contact_id}: {type(exc).__name__}: {exc}"[:200])
    logger.info(
        "과거 기록 이관: 연락처 %d명 처리, 기록 %d건 추가, 실패 %d명",
        len(candidates), inserted, len(failed),
    )
    return {
        "processed": len(candidates),
        "inserted": inserted,
        "failed": failed,
        "remaining_estimate": max(0, (total or 0) - len(candidates)),
        "contacts_in_scope": total,
    }


@router.post("/customers/{contact_id}/sync")
async def customer_sync(contact_id: int):
    try:
        await asyncio.to_thread(_sync_hubspot, contact_id)
    except Exception as exc:
        logger.warning("Customer HubSpot sync failed for %d", contact_id, exc_info=True)
        raise HTTPException(status_code=502, detail=f"HubSpot 동기화 실패: {exc}") from exc
    return RedirectResponse(f"/customers/{contact_id}", status_code=303)


@router.get("/pipeline")
async def pipeline_board_redirect():
    """The board moved onto the dashboard; keep bookmarks and old links working.

    The POST actions below keep their /pipeline/... paths — security.py:24 and the
    board's own drag-drop fetch are both pinned to that prefix — so only the page
    itself moved. They now redirect to / where the board actually renders.
    """
    return RedirectResponse("/", status_code=308)


# 보드가 이 응답의 **최종 주소**에서 ?sync 를 읽습니다(SyncBanner.syncStateFrom). 그래서
# 목적지가 `/app` 이어야 합니다: `/` 로 보내면 legacy_redirects 가 `/app` 으로 한 번 더
# 넘기면서 쿼리를 떨어뜨려, 성공했을 때 배너가 **한 번도 뜬 적이 없었습니다** — 운영자가
# 본 것은 catch 로 들어가는 "partial" 뿐이었습니다. 왕복 두 번도 같이 없어집니다.
_BOARD_REDIRECT = "/app?sync={sync}#stage-{stage}"


@router.post("/pipeline/{contact_id}/stage")
async def pipeline_stage_move(contact_id: int, stage: str = Form(...)):
    ticket_id, sheet_client_id = _set_local_stage(contact_id, stage)
    result = await _sync_stage(ticket_id, stage, contact_id, sheet_client_id)
    return RedirectResponse(
        _BOARD_REDIRECT.format(sync=_sync_state(result), stage=stage), status_code=303
    )


@router.post("/pipeline/conversations/{conversation_id}/stage")
async def pipeline_inquiry_stage_move(conversation_id: int, stage: str = Form(...)):
    """The board's drop target. The local move is committed first and always sticks;
    HubSpot and the workbook follow, and the ?sync flag says which of them actually did."""
    ticket_id, contact_id, sheet_client_id = _set_conversation_stage(conversation_id, stage)
    result = await _sync_stage(ticket_id, stage, contact_id, sheet_client_id)
    return RedirectResponse(
        _BOARD_REDIRECT.format(sync=_sync_state(result), stage=stage), status_code=303
    )


@router.post("/pipeline/conversations/{conversation_id}/deal-detail")
async def pipeline_deal_detail(conversation_id: int, detail: str = Form("")):
    """Won Type / Lost Reason 을 그 문의에 붙입니다. 우리 DB 에만 씁니다.

    HubSpot 으로 보내지 않는 이유: 그 파이프라인에 대응하는 속성이 있는지 확인되지 않았고,
    없는 속성에 쓰면 요청마다 400 이 납니다. 값이 어느 목록에서 왔는지는 **그 문의의 현재
    단계**가 정합니다 — Won 카드에 Lost 사유를 붙일 수 있으면 그 값은 아무 뜻이 없습니다.
    """
    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다")
        allowed = DEAL_DETAILS.get(conversation.stage or "", ())
        if not allowed:
            raise HTTPException(status_code=400, detail="이 단계에는 Deal Detail 이 없습니다")
        value = detail.strip()
        if value and value not in allowed:
            raise HTTPException(status_code=400, detail="지원하지 않는 Deal Detail 값입니다")
        conversation.deal_detail = value or None
        session.commit()
    return {"ok": True}


@router.post("/pipeline/backfill")
async def pipeline_backfill(request: Request):
    """Queue the one-shot HubSpot -> DB backfill (admin only).

    Records a request and returns immediately; the poller performs the work on its
    next tick. HubSpot is read-only here and no mail can result — see
    src/agents/hubspot_backfill.py.
    """
    from ...agents.hubspot_backfill import request_hubspot_backfill
    from ..auth import actor_name

    _require_integration_admin(request)
    try:
        request_hubspot_backfill(actor_name(request, "local-admin"))
    except Exception as exc:
        logger.warning("HubSpot backfill could not be queued.", exc_info=True)
        return RedirectResponse(
            f"/?backfill=error&detail={quote(str(exc)[:180])}",
            status_code=303,
        )
    return RedirectResponse("/?backfill=queued", status_code=303)


def _google_callback_url(request: Request) -> str:
    base = settings.PUBLIC_BASE_URL.strip().rstrip("/")
    if not base:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        base = f"{scheme}://{request.url.netloc}"
    return f"{base}/integrations/google-sheets/callback"


def _require_integration_admin(request: Request) -> None:
    if settings.AUTH_MODE != "google_oauth":
        return
    from ..auth import admin_required

    if not admin_required(request):
        raise HTTPException(status_code=403, detail="관리자만 외부 계정을 연결할 수 있습니다")


@router.get("/integrations/google-sheets/connect")
async def google_sheets_connect(request: Request):
    from ...integrations.google_oauth import authorization_url, make_state

    _require_integration_admin(request)
    try:
        state = make_state()
        url = authorization_url(_google_callback_url(request), state)
    except Exception as exc:
        logger.warning("Google Sheets OAuth start failed.", exc_info=True)
        return RedirectResponse(
            f"/?google=setup_required&detail={quote(str(exc)[:180])}",
            status_code=303,
        )
    response = RedirectResponse(url, status_code=302)
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        GOOGLE_SHEETS_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=proto == "https",
        samesite="lax",
        path="/integrations/google-sheets/callback",
    )
    return response


@router.get("/integrations/google-sheets/callback")
async def google_sheets_callback(
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
):
    from ...integrations.google_oauth import exchange_code, validate_state

    _require_integration_admin(request)
    try:
        expected_state = request.cookies.get(GOOGLE_SHEETS_STATE_COOKIE, "")
        if not expected_state or not hmac.compare_digest(expected_state, state):
            raise ValueError("Google 연결 요청 세션이 일치하지 않습니다. 다시 연결해 주세요.")
        validate_state(state)
        if error:
            raise ValueError(f"Google 연결이 취소되었습니다: {error[:120]}")
        if not code:
            raise ValueError("Google authorization code is missing.")
        _payload, account_email = await exchange_code(code, _google_callback_url(request))
    except Exception as exc:
        logger.warning("Google Sheets OAuth callback failed.", exc_info=True)
        response = RedirectResponse(
            f"/?google=error&detail={quote(str(exc)[:180])}",
            status_code=303,
        )
        response.delete_cookie(
            GOOGLE_SHEETS_STATE_COOKIE, path="/integrations/google-sheets/callback"
        )
        return response
    logger.info("Google Sheets user OAuth connected for %s.", account_email or "unknown account")
    response = RedirectResponse("/?google=connected", status_code=303)
    response.delete_cookie(
        GOOGLE_SHEETS_STATE_COOKIE, path="/integrations/google-sheets/callback"
    )
    return response


@router.post("/integrations/google-sheets/disconnect")
async def google_sheets_disconnect(request: Request):
    from ...integrations.google_oauth import delete_grant, env_grant

    _require_integration_admin(request)
    if env_grant() is not None:
        # Deleting the row would not disconnect anything — env wins in load_grant().
        # Say so rather than reporting a disconnect that did not happen.
        detail = quote("GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN 으로 연결된 계정입니다. 해제하려면 그 환경변수를 비우세요.")
        return RedirectResponse(
            f"/?google=error&detail={detail}", status_code=303
        )
    delete_grant()
    return RedirectResponse("/?google=disconnected", status_code=303)


@router.post("/integrations/google-sheets/sync")
async def google_sheets_sync(request: Request):
    from ...agents.sheet_sync import request_full_sheet_sync
    from ..auth import actor_name

    _require_integration_admin(request)
    try:
        request_id = request_full_sheet_sync(actor_name(request, "local-admin"))
    except Exception as exc:
        logger.warning("Google Sheets manual synchronization failed.", exc_info=True)
        return RedirectResponse(
            f"/?google=error&detail={quote(str(exc)[:180])}",
            status_code=303,
        )
    return RedirectResponse(
        f"/?google=queued&request_id={request_id}",
        status_code=303,
    )


def _contract_rows(*, status: str = "", query: str = "") -> list[dict]:
    """수주 고객 — every contract with the customer it belongs to, newest first.

    One join, not a contract dump plus a contact lookup per row: the screen shows the
    customer's name on every line, so the name comes back with the line.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        statement = (
            select(ContractRecord, Contact)
            .join(Contact, Contact.id == ContractRecord.contact_id)
            .order_by(ContractRecord.contract_date.desc().nullslast(), ContractRecord.id.desc())
        )
        if status:
            statement = statement.where(ContractRecord.status == status)
        loaded = session.execute(statement).all()

    needle = query.strip().lower()
    rows: list[dict] = []
    for contract, contact in loaded:
        if needle and needle not in " ".join(
            filter(None, [contact.full_name, contact.email, contact.company, contact.domain,
                          contract.plan])
        ).lower():
            continue
        expires = contract.expires_at
        rows.append(
            {
                "id": contract.id,
                "contact_id": contact.id,
                "company": contact.company or contact.full_name,
                "name": contact.full_name,
                "email": contact.email,
                "status": contract.status,
                "plan": contract.plan,
                "amount": contract.amount,
                "currency": contract.currency,
                "payment_method": contract.payment_method,
                "contract_date": contract.contract_date,
                "payment_due_at": contract.payment_due_at,
                "paid_at": contract.paid_at,
                "expires_at": expires,
                # Negative means it already lapsed. None when no expiry was recorded —
                # which is not the same as "never expires" and must not render as 0.
                "days_to_expiry": (expires - now).days if expires else None,
                "conversation_id": contract.conversation_id,
                "sheet_client_id": contract.sheet_client_id,
                "unit_price": contract.unit_price,
                "language_pairs": contract.language_pairs or [],
            }
        )
    return rows


def _contract_summary() -> dict:
    """The money line on 전체 대시보드 and 수주 고객.

    Amounts are summed PER CURRENCY. A single total would add ₩ to $ — the workbook
    holds both, and one number covering both currencies is worse than no number.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    horizon = now + timedelta(days=RENEWAL_WINDOW_DAYS)
    with SessionLocal() as session:
        by_status = dict(
            session.execute(
                select(ContractRecord.status, func.count()).group_by(ContractRecord.status)
            ).all()
        )
        active_money = session.execute(
            select(ContractRecord.currency, func.sum(ContractRecord.amount))
            .where(ContractRecord.status.in_(("contracted", "active")))
            .group_by(ContractRecord.currency)
        ).all()
        expiring = session.scalar(
            select(func.count())
            .select_from(ContractRecord)
            .where(
                ContractRecord.status == "active",
                ContractRecord.expires_at.is_not(None),
                ContractRecord.expires_at <= horizon,
            )
        )
        overdue = session.scalar(
            select(func.count())
            .select_from(ContractRecord)
            .where(
                ContractRecord.paid_at.is_(None),
                ContractRecord.payment_due_at.is_not(None),
                ContractRecord.payment_due_at < now,
                ContractRecord.status.in_(("sent", "contracted", "active")),
            )
        )
    return {
        "total": sum(by_status.values()),
        "by_status": {status: by_status.get(status, 0) for status, _ in CONTRACT_STATUS_LABELS},
        "active_amounts": [
            {"currency": currency, "amount": amount} for currency, amount in active_money
        ],
        "expiring_soon": expiring or 0,
        "renewal_window_days": RENEWAL_WINDOW_DAYS,
        "payment_overdue": overdue or 0,
    }



def _operations_context() -> dict:
    """고객 인사이트 — 손이 가야 하는 고객 목록들.

    Extracted from the route so the React screen reads the same numbers — a second copy
    of this arithmetic is a second set of answers to 몇 건이냐.

    「리드 추이」(기간별 문의 수 막대 · 국가별 비중 · 평균 점수)가 여기서 같이 나왔습니다.
    보는 사람이 없어 화면과 함께 지웠습니다 — 화면에서만 빼면 매 요청마다 아무도 안 읽는
    집계가 계속 돕니다(대화 전체를 훑는 계산이었습니다).
    """
    rows = _customer_rows()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_before = now - timedelta(days=14)
    reminder_1_before = now - timedelta(days=FOLLOW_UP_REMINDER_1_DAYS)
    reminder_2_before = now - timedelta(days=FOLLOW_UP_REMINDER_2_DAYS)
    unqualified_before = now - timedelta(days=FOLLOW_UP_UNQUALIFIED_DAYS)
    renew_before = now + timedelta(days=60)
    with SessionLocal() as session:
        conversations = session.execute(select(Conversation)).scalars().all()
        renewals = (
            session.execute(
                select(ContractRecord, Contact)
                .join(Contact, ContractRecord.contact_id == Contact.id)
                .where(
                    ContractRecord.status == "active",
                    ContractRecord.expires_at.isnot(None),
                    ContractRecord.expires_at <= renew_before,
                )
                .order_by(ContractRecord.expires_at)
            )
            .all()
        )
    row_by_contact = {row["contact"].id: row for row in rows}
    stale = [
        row
        for row in rows
        if row["state"] == "negotiation"
        and row["last_activity"]
        and row["last_activity"].replace(tzinfo=None) < stale_before
    ]
    missing_reply = []
    due_reminder_1 = []
    due_reminder_2 = []
    due_unqualified = []
    for conv in conversations:
        incoming = _naive(conv.last_incoming_at)
        outgoing = _naive(conv.last_outgoing_at)
        if incoming and (not outgoing or incoming > outgoing):
            row = row_by_contact.get(conv.contact_id)
            if row and row not in missing_reply:
                missing_reply.append(row)
            continue
        # Waiting on the customer: we mailed last and they have not answered since.
        # Buckets are exclusive so the counts add up - each thread shows on the one
        # rung of the ladder it currently sits at.
        if not outgoing or (incoming and incoming >= outgoing):
            continue
        row = row_by_contact.get(conv.contact_id)
        if not row:
            continue
        if outgoing < unqualified_before:
            bucket = due_unqualified
        elif outgoing < reminder_2_before:
            bucket = due_reminder_2
        elif outgoing < reminder_1_before:
            bucket = due_reminder_1
        else:
            continue
        if row not in bucket:
            bucket.append(row)
    lost = [row for row in rows if row["stage"] == "closed_lost" or row["state"] == "lost"]
    upsell = [
        row
        for row in rows
        if row["state"] == "service"
        and (not row["profile"] or (row["profile"].current_plan or "").lower() not in {"business", "enterprise"})
    ]
    return {
            "stale": stale,
            "missing_reply": missing_reply,
            "due_reminder_1": due_reminder_1,
            "due_reminder_2": due_reminder_2,
            "due_unqualified": due_unqualified,
            "follow_up_days": {
                "reminder_1": FOLLOW_UP_REMINDER_1_DAYS,
                "reminder_2": FOLLOW_UP_REMINDER_2_DAYS,
                "unqualified": FOLLOW_UP_UNQUALIFIED_DAYS,
            },
            "renewals": renewals,
            "lost": lost,
            "upsell": upsell,
    }


