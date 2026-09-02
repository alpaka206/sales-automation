"""Message list, detail, and approval-action (send/reject/edit) routes."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import joinedload

from ...agents.approval import ApprovalError, approve, reject
from ...common.config import settings
from ...common.subjects import reply_subject, strip_reply_prefixes
from ...common.textwash import text_wash
from ...db.conversation_history import ROUTINE_PROGRESS_KINDS, add_progress
from ...common.inquiry import CATEGORY_LABELS, UNQUALIFIED, category_label, is_unqualified
from ...db.email_templates import list_signature_templates
from ...db.models import (
    Client,
    Contact,
    Conversation,
    ConversationProgress,
    CustomerInteraction,
    CustomerProfile,
    Message,
)
from ...db.session import SessionLocal
from ...llm.translate import is_mostly_korean, needs_korean, translate_to
from ..auth import actor_name
from .customer_ops import won_block
from ._shared import esc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])

# Maximum bytes accepted for a single edit — prevents accidental/malicious DoS via huge POST.
def list_now() -> datetime:
    """The clock a list is dated against, truncated to the minute.

    It rides in the payload so every row's "waited N days" is measured against one
    instant instead of each browser's own clock. Truncated because a microsecond-precise
    timestamp changes the response body on every single request, which defeats the
    conditional GET in front of it — the console would re-download an identical list every
    15 seconds forever. The dot it feeds is bucketed in DAYS; a minute of coarseness is
    invisible to it and turns three of four polls into a 304.
    """
    # Rounded UP, not down. Truncating alone puts `now` up to 59 seconds in the past,
    # and a row created moments ago then reports a NEGATIVE age against it.
    return (datetime.now(timezone.utc) + timedelta(minutes=1)).replace(second=0, microsecond=0)


_MAX_EDIT_BODY_BYTES = 100_000

_MAX_EDIT_SUBJECT_LEN = 300


def _clean_signature_key(value: str | None) -> str | None:
    """The posted signature choice, or None for 서명 없음.

    Anything that is not an active signature — the empty option, a stale ``"none"`` from
    when 기본 텍스트 서명 was a third choice, a forged key — becomes None, so a bad value
    can never select an arbitrary template. There is nothing to distinguish any more:
    "no signature" is what None means now that nothing writes one into the body.
    """
    v = (value or "").strip()
    return v if v in {s["key"] for s in list_signature_templates()} else None


def _message_detail_context(
    message_id: int | None = None, *, conversation_id: int | None = None
) -> dict:
    """Load one ticket with its related customer data. **메일이 없어도 열립니다.**

    두 가지 방법으로 부릅니다:

    - ``message_id`` — 회신 및 검토 목록에서 그 초안을 열 때. 그 메일이 화면의 「현재」 글이
      되고, 검토 중이면 편집기가 그 자리에 그려집니다.
    - ``conversation_id`` — 보드 카드에서 티켓을 열 때. 그 대화의 마지막 메일이 현재 글이고,
      **메일이 하나도 없으면 그대로 없는 채로** 티켓 정보·Deal Detail·소통 히스토리만 그립니다.

    뒤엣것이 필요한 이유: ``hubspot_backfill`` 은 티켓에서 대화만 만들고 메일 행은 만들지
    않습니다. HubSpot 에서 들여온 Won·Lost 티켓이 전부 그렇고, 그래서 보드에서 그 카드를
    누르면 티켓이 아니라 **고객 페이지**로 빠졌습니다 — Deal Detail 은 티켓의 값인데 티켓을
    열 방법이 없었던 셈입니다.
    """
    from .customer_ops import visible_deal_detail

    with SessionLocal() as session:
        msg = None
        if message_id is not None:
            msg = (
                session.execute(
                    select(Message)
                    .options(
                        joinedload(Message.conversation).joinedload(Conversation.contact),
                    )
                    .where(Message.id == message_id)
                )
                .unique()
                .scalar_one_or_none()
            )
            if not msg:
                return {}
            conv = msg.conversation
        else:
            conv = (
                session.execute(
                    select(Conversation)
                    .options(joinedload(Conversation.contact))
                    .where(Conversation.id == conversation_id)
                )
                .unique()
                .scalar_one_or_none()
            )
            if conv is None:
                return {}
            # 마지막 메일이 「현재」 글입니다. 그것이 검토 대기 중인 초안이면 편집기가
            # 열리는데, 보드에서 눌러 들어온 사람에게도 그게 맞는 화면입니다.
            msg = session.scalars(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.id.desc())
                .limit(1)
            ).first()

        contact = conv.contact if conv else None

        thread_rows = []
        progress_rows = []
        interaction_rows = []
        other_conversations = []
        won_summary = None
        if conv:
            # Full ticket/conversation thread, oldest → newest — every inbound inquiry
            # and every outgoing reply or auto-ack for this thread.
            thread_rows = (
                session.execute(
                    select(Message)
                    .where(Message.conversation_id == conv.id)
                    .order_by(Message.created_at.asc(), Message.id.asc())
                )
                .scalars()
                .all()
            )
            # 처리 경과, oldest → newest. Filtered on READ, never deleted: progress rows
            # are append-only, and what the machine did to itself is still worth having
            # in the row when something has to be explained.
            progress_rows = (
                session.execute(
                    select(ConversationProgress)
                    .where(ConversationProgress.conversation_id == conv.id)
                    .where(ConversationProgress.kind.not_in(ROUTINE_PROGRESS_KINDS))
                    .order_by(ConversationProgress.created_at.asc(), ConversationProgress.id.asc())
                )
                .scalars()
                .all()
            )
            # The other half of the story: after the first reply the thread leaves
            # HubSpot for mail, WhatsApp, a phone call or a meeting, and only the
            # operator knows what was said. Those notes belong on this log, not on a
            # separate one — "메일이 나갔다 → 미팅했고 요구사항은 이것" is one sequence.
            interaction_rows = (
                session.execute(
                    select(CustomerInteraction)
                    .where(CustomerInteraction.conversation_id == conv.id)
                    .order_by(CustomerInteraction.happened_at.asc(), CustomerInteraction.id.asc())
                )
                .scalars()
                .all()
            )
            # 수주 고객인가. 정식 연결(`clients.contact_id`)이 먼저지만 운영 DB 에서 그
            # 값이 거의 비어 있어(고객 추가 폼이 안 받습니다) Client ID 로도 찾습니다.
            client_ids = {
                cid
                for cid in (conv.sheet_client_id, contact.sheet_client_id if contact else None)
                if cid
            }
            won_client = (
                session.execute(select(Client).where(Client.contact_id == conv.contact_id))
                .scalars()
                .first()
            )
            if won_client is None and client_ids:
                won_client = (
                    session.execute(select(Client).where(Client.client_id.in_(client_ids)))
                    .scalars()
                    .first()
                )
            won_summary = won_block(won_client, list(won_client.contracts) if won_client else [])

            # 같은 사람의 다른 티켓. 이 화면에서 「이 고객 건이 또 있나」를 물으려고 리드
            # 히스토리를 열었다 돌아오는 왕복이 있었습니다.
            other_conversations = (
                session.execute(
                    select(Conversation)
                    .where(
                        Conversation.contact_id == conv.contact_id,
                        Conversation.id != conv.id,
                    )
                    .order_by(Conversation.created_at.desc())
                )
                .scalars()
                .all()
            )

        # Customer-level history (CRM state, contract, cross-channel touchpoints)
        # surfaced inline so the operator sees who this customer is without leaving
        # the reply screen. Full editable view stays at /customers/{id}.
        customer = (
            _customer_history(
                session, contact.id, exclude_conversation_id=conv.id if conv else None
            )
            if contact
            else None
        )

        # The customer's inquiry is shown TRANSLATED (Korean) by default with the
        # original behind an expand toggle. ``needs_ko`` flags inbound non-Korean
        # bubbles; ``body_ko``/``subject_ko`` are filled by the route (concurrently)
        # so the page already shows Korean without a click.
        thread = [
            {
                "id": tm.id,
                "direction": tm.direction,
                "status": tm.status,
                "subject": tm.subject,
                "body": tm.body,
                # **본문이 기준입니다** (2026-08-19 운영자 지시). 전에는 제목이 영문이기만
                # 해도 번역 UI 가 켜졌습니다 — 한국어로 온 문의인데 제목이 "Custom Quote"
                # 인 흔한 경우에 「원문 보기」가 뜨고, 눌러 봐야 같은 한국어 본문입니다.
                # 제목 한 줄은 영문이어도 읽는 데 걸림돌이 아닙니다.
                #
                # 본문이 비어 있을 때만 제목으로 판단합니다: 그때는 제목이 곧 문의 전부라,
                # 영문이면 번역이 필요합니다.
                "needs_ko": tm.direction == "inbound"
                and (
                    needs_korean(tm.body)
                    or (not (tm.body or "").strip() and needs_korean(tm.subject or ""))
                ),
                "body_ko": tm.body_ko,
                "subject_ko": tm.subject_ko,
                "is_auto_ack": tm.prompt_variant == "auto_ack",
                # 한 줄 요약. New 를 지난 화면은 본문 대신 이것을 보여 주고,
                # 「전체보기」를 눌렀을 때 본문이 나옵니다.
                "summary_line": tm.summary_line,
                "language": tm.language,
                "channel": tm.channel,
                "from_address": tm.from_address,
                "to_address": tm.to_address,
                "created_at": tm.created_at,
                "sent_at": tm.sent_at,
                "is_current": msg is not None and tm.id == msg.id,
            }
            for tm in thread_rows
        ]

        return {
            "thread": thread,
            # **진행 기록만.** 한동안 이 목록에 소통 기록(`interaction_rows`)을 같은
            # 모양으로 섞어 보냈는데, 그건 바로 아래 `ticket_interactions` 로도 나가는
            # **같은 행**입니다. 화면이 둘 다 그리면서 기록 하나가 두 번 보였고, 그중
            # 한쪽은 `i.summary` — 메일 **본문 전체**를 회색 한 줄에 그대로 쏟았습니다
            # (2026-08-20 운영자 지적). 섞어 보내던 시절에는 소통 기록이 이 화면에
            # 나올 길이 저것뿐이었지만, 지금은 제 카드에서 접힌 채로 그려집니다.
            "progress": sorted(
                [
                    {"kind": p.kind, "detail": p.detail, "created_at": p.created_at}
                    for p in progress_rows
                ],
                key=lambda entry: entry["created_at"],
            ),
            # **이 티켓 밖의 것들.** 지금 처리할 것(위의 스레드·초안)과 섞지 않고 따로
            # 내려보냅니다 — 판단에 필요한 맥락이지 이 티켓에서 일어난 일이 아닙니다.
            "other_tickets": [
                {
                    "conversation_id": other.id,
                    "ticket_id": other.hubspot_ticket_id,
                    "subject": other.inquiry_subject,
                    "stage": other.stage,
                    "created_at": other.created_at,
                    # **이미 있는 요약을 씁니다.** 접수할 때 모델이 뽑아 둔 값이라
                    # (`conversations.customer_requests`) 여기서 다시 만들 이유가 없습니다.
                    # 제목만 있으면 「자막 번역 견적」이 무엇을 물은 건지 알 수 없습니다.
                    "requests": other.customer_requests or other.summary,
                }
                for other in other_conversations
            ],
            # **돈이 오갔는가.** 수주 고객에게 쓰는 답장은 톤부터 다릅니다 — 지금까지 이
            # 화면에는 그 사실이 없어서, 계약이 도는 고객인지 확인하려면 수주 화면으로
            # 나갔다 와야 했습니다. 계약의 원본은 그쪽이고 여기는 거울입니다.
            "won": won_summary,
            # `summary` 는 안 보냅니다 — 화면의 요약 카드를 뺐습니다(2026-08-25). 그
            # 불릿은 「이 티켓의 기록」 각 줄의 둘째 줄과 같은 문자열이라 같은 화면이 같은
            # 말을 두 번 했습니다. 값 자체는 계속 쌓이고 초안 프롬프트가 읽습니다.
            "customer_requests": conv.customer_requests if conv else None,
            "category": conv.inquiry_category if conv else None,
            "category_label": category_label(conv.inquiry_category if conv else None),
            "unqualified": is_unqualified(conv.inquiry_category if conv else None),
            "signatures": list_signature_templates(),
            "ticket": {
                "id": conv.id if conv else None,
                "ticket_id": conv.hubspot_ticket_id if conv else None,
                "stage": conv.stage if conv else None,
                # Won Type / Lost Reason. 보드 카드와 **같은 값·같은 규칙**입니다: 지금
                # 단계의 목록에 없는 값은 안 내려보냅니다(Won 에서 고른 값이 Lost 사유
                # 자리에 붙으면 안 됩니다). 값 자체는 지우지 않으므로 되돌아오면 다시 뜹니다.
                "deal_detail": visible_deal_detail(
                    conv.stage if conv else None, conv.deal_detail if conv else None
                ),
                "inquiry_subject": conv.inquiry_subject if conv else None,
                "inquiry_language": conv.inquiry_language if conv else None,
                # The Inbound DB workbook's stable key for this inquiry (e.g. 1330).
                # Threads this app appended carry it on the conversation; ones imported
                # from the sheet carry it on the contact — same fallback order every
                # stage-sync path uses.
                "client_id": (
                    (conv.sheet_client_id if conv else None)
                    or (contact.sheet_client_id if contact else None)
                ),
            },
            # This ticket's own manual touchpoints — what happened on email, WhatsApp,
            # phone or SMS after the first reply. Contact-wide records (logged from
            # 리드 히스토리, or synced from HubSpot) carry no conversation_id and stay
            # in the sidebar's 접점 기록 instead.
            "ticket_interactions": (
                [
                    {
                        "id": it.id,
                        "channel": it.channel,
                        "direction": it.direction,
                        "handler": it.handler,
                        "subject": it.subject,
                        "summary": it.summary,
                        "context": it.context,
                        "artifact_url": it.artifact_url,
                        "happened_at": it.happened_at,
                    }
                    # 위에서 이미 읽어 둔 같은 행들입니다(오래된 순). 여기서 최신순으로
                    # 한 번 더 SELECT 하고 있었는데, 조건도 같고 세션도 같아 결과가 같습니다
                    # — 뒤집기만 하면 됩니다. 왕복 하나가 200ms 인 데다 이 화면이 제일 자주
                    # 열립니다.
                    for it in reversed(interaction_rows)
                ]
                if conv
                else []
            ),
            # 메일이 하나도 없는 티켓은 `None` 입니다 — HubSpot 에서 들여온 티켓이 그렇고,
            # 그때 화면은 초안 편집기와 발송 정보 칸을 아예 그리지 않습니다.
            "msg": {
                "id": msg.id,
                "status": msg.status,
                # 발송이 실패한 이유. 운영자가 발송을 누른 자리가 여기라서 결과도 여기에
                # 섭니다 — 복구 화면까지 가야 이유를 볼 수 있으면 그건 「실패」 배지 하나와
                # 다를 게 없습니다.
                "send_error": msg.send_error,
                "subject": msg.subject or "",
                "body": msg.body,
                # 번역이 덮어쓰기 **전**의 한국어 초안. 번역 전에는 `None` 입니다
                # (본문 자체가 그 한국어라 옆에 한 벌 더 둘 이유가 없습니다).
                "body_ko": msg.body_ko,
                "channel": msg.channel,
                "direction": msg.direction,
                # Every thread starts from an inbound inquiry.
                "flow": "inbound_reply",
                "language": msg.language,
                "target_language": msg.target_language,
                "signature_key": msg.signature_key or "",
                "to_address": msg.to_address or "",
                "from_address": msg.from_address or "",
                "score_snapshot": msg.score_snapshot,
                "scheduled_at": msg.scheduled_at,
                "sent_at": msg.sent_at,
                "created_at": msg.created_at,
            }
            if msg is not None
            else None,
            "contact": (
                {
                    "id": contact.id,
                    "name": contact.full_name,
                    "email": contact.email,
                    "company": contact.company,
                    "domain": contact.domain,
                    "role_description": contact.role_description,
                    # MQL / PQL. **플랜에서 나오는 계산값**이라 저장한 열을 읽지 않습니다
                    # (2026-09-02 운영자 지시) — `customer_profiles.qualification` 은
                    # 워크북에서 읽어 온 거울이고 콘솔에서 채우는 길이 없어 늘 비어
                    # 있었습니다. 그래서 화면에도 「-」만 떴습니다.
                    "qualification": _qualification_of(customer),
                }
                if contact
                else None
            ),
            "customer": customer,
        }


def _qualification_of(customer: dict | None) -> str:
    """그 연락처의 MQL / PQL. 프로필 행이 없으면 MQL — 산 적이 없다는 뜻입니다."""
    from ...common.sheet_values import qualification_for_plan

    profile = (customer or {}).get("profile") or {}
    return qualification_for_plan(profile.get("current_plan"))


def _customer_history(session, contact_id: int, exclude_conversation_id: int | None = None) -> dict:
    """Read-only customer-level history for the message-detail sidebar.

    Mirrors the pieces of the /customers/{id} page that are NOT already on the
    reply screen: the CustomerProfile snapshot (pipeline/state/temperature/next
    action), the latest contract, and the cross-channel touchpoint log
    (CustomerInteraction — manual notes + HubSpot-synced emails/deals/notes).
    Everything is serialized to plain dicts before the session closes, so the
    template never touches a detached ORM object. Editing lives at /customers/{id}.

    ``exclude_conversation_id`` drops the records THIS ticket already lists in its own
    소통 히스토리 card — otherwise every call the operator logs here would render twice on
    one screen.

    계약(`ContractRecord`)도 여기서 같이 실어 보냈습니다. 화면이 읽지 않아 지웠습니다 —
    티켓 하나 열 때마다 계약 테이블을 한 번 더 읽고 버리는 값이었습니다. 계약을 보는 곳은
    고객 상세입니다.
    """
    profile = session.get(CustomerProfile, contact_id)
    interaction_q = select(CustomerInteraction).where(
        CustomerInteraction.contact_id == contact_id
    )
    if exclude_conversation_id is not None:
        interaction_q = interaction_q.where(
            (CustomerInteraction.conversation_id.is_(None))
            | (CustomerInteraction.conversation_id != exclude_conversation_id)
        )
    interactions = (
        session.execute(
            # 6 → 20 (2026-08-19). 허브스팟에서 옛 기록을 끌어온 뒤로 6건은 최근 며칠
            # 밖에 못 보여 줍니다. 화면에서는 접혀 있으므로 길어도 자리를 안 먹습니다.
            interaction_q.order_by(CustomerInteraction.happened_at.desc()).limit(20)
        )
        .scalars()
        .all()
    )
    profile_data = (
        {
            "customer_state": profile.customer_state,
            "pipeline_stage": profile.pipeline_stage,
            "lead_temperature": profile.lead_temperature,
            "current_plan": profile.current_plan,
            "next_action": profile.next_action,
            "next_action_at": profile.next_action_at,
        }
        if profile
        else None
    )
    interaction_rows = [
        {
            "channel": it.channel,
            "direction": it.direction,
            "handler": it.handler,
            "subject": it.subject,
            "summary": it.summary,
            # 가져올 때 만들어 둔 한 줄. 목록은 이것을 먼저 보여 주고 본문은 눌러야 나옵니다 —
            # 제목만으로는 「자막 번역 견적」이 무엇을 물은 건지 알 수 없습니다.
            "digest": it.context,
            "happened_at": it.happened_at,
        }
        for it in interactions
    ]
    return {
        "profile": profile_data,
        "interactions": interaction_rows,
    }


# 여기 있던 `_translate_inbound_bubbles` 는 지웠습니다. **화면을 여는 길에는 모델이 없습니다.**
#
# 고객 문의를 한국어로 옮기는 일은 접수할 때 한 번 하고 행에 넣어 둡니다
# (`inbound.cache_korean_inquiries`). 그 함수가 유일한 자리입니다. 열 때 하면 그 티켓을
# 처음 여는 사람이 매번 Gemini 를 기다렸다가 화면을 보고, 번역이 한 번 실패하면 그 티켓은
# 영원히 느려집니다 — 답을 쓰려고 여는 창인데 말입니다.
#
# 회신 초안은 여기서 손대지 않습니다. 초안은 원래 한국어로 쓰이고, 보낼 언어로 바꾸는 것은
# 운영자가 검토 화면에서 `번역하기` 를 누를 때뿐입니다. 미리 해 둘 것은 **한국어가 아닌
# 고객 문의** 하나뿐입니다.
#
# 아직 안 채워진 옛 행은 화면이 원문을 그대로 보여 주고(`body_ko || body`), 10분 폴러가
# 조금씩 채웁니다.


# The two status buckets the operator reviews. "발송대기" is everything a human still
# has to act on; "발송완료" is everything finished, with 거절 distinguished in the row.
# Deliberately absent: "approved" (queued, nothing to decide) and "delivery_unknown"
# (resolved on the 운영 로그 복구 tab, which owns that workflow). A row mid-send carries
# a transient "sending:<pid>:<rand>" status and matches neither, so it hides for the
# few seconds it is claimed instead of rendering that token as a pill.
LIST_STATUS_BUCKETS: dict[str, tuple[str, ...]] = {
    "awaiting": ("pending_approval", "drafting", "draft_failed", "send_failed"),
    # `superseded` 는 없어졌습니다 (2026-08-19). 단계가 넘어가 뜻을 잃은 초안은 이제
    # **지웁니다** — 여기 두면 고객이 본 적 없는 글이 「발송 완료」로 보였습니다. 이관
    # 0079 가 그 전에 쌓인 행도 치웠습니다.
    "sent": ("sent", "test_sent", "rejected"),
}
# Stage chips, per status bucket ("" = 전체). The two buckets sit at opposite ends of the
# pipeline, so one shared chip row was wrong in both directions: a reply still waiting
# belongs to a ticket nobody has answered (New) or one under negotiation, and nothing
# else — while sending is exactly what moves a ticket PAST New, so 발송 완료 never has a
# New row and does have the downstream stages. Chips are rendered in PIPELINE_STAGES
# order; a stage that is not in the current bucket falls back to 전체 (see below), which
# is what happens when the operator switches buckets with a stage chip active.
LIST_STAGES: dict[str, tuple[str, ...]] = {
    # 발송 대기 is New only. Drafts are generated for New tickets and nothing else
    # (InboundAgent.handle returns "skipped_not_new" for any other stage), so a
    # Negotiating chip here could only ever return an empty table.
    "awaiting": ("new",),
    "sent": (
        "meeting_link_sent", "negotiation", "reminder_sent",
        "won", "closed_lost", "closed",
    ),
}
LIST_SORTS = ("oldest", "newest")


def _messages_list_context(
    status: str = "awaiting",
    stage: str = "",
    sort: str = "oldest",
) -> dict:
    """The approval queue: outgoing drafts and finished replies, never inbound rows.

    Every parameter is validated against a fixed set before it reaches SQL or the
    polling URL in the template — the same allow-list discipline as
    ``VALID_PIPELINE_STAGES``. Unvalidated values would be interpolated into the
    template's ``hx-get`` attribute, where an ``&`` survives escaping and appends a
    parameter to the 15-second poll.
    """
    from .customer_ops import PIPELINE_STAGES, VALID_PIPELINE_STAGES

    status = status if status in LIST_STATUS_BUCKETS else "awaiting"
    # Validated against the CHOSEN bucket's stages, so this also drops a stage the
    # operator carried over from the other bucket instead of returning an empty list.
    stage = stage if stage in LIST_STAGES[status] else ""
    sort = sort if sort in LIST_SORTS else "oldest"

    # Conversation is already joined, so stage / inquiry_subject / created_at /
    # last_incoming_at are select-list additions. Only Contact is new, and
    # Conversation.contact_id is NOT NULL so an inner join loses nothing.
    q = (
        select(
            Message,
            Conversation.stage,
            Conversation.inquiry_subject,
            Conversation.inquiry_category,
            Conversation.created_at,
            Conversation.last_incoming_at,
            Contact.email,
        )
        .join(Conversation, Message.conversation_id == Conversation.id)
        .join(Contact, Conversation.contact_id == Contact.id)
        .where(Message.direction == "outgoing")
        # Auto-ack (접수확인) replies are sent automatically and shown inside the
        # thread — keep them out of the approval queue list so it isn't noisy.
        .where((Message.prompt_variant.is_(None)) | (Message.prompt_variant != "auto_ack"))
        .where(Message.status.in_(LIST_STATUS_BUCKETS[status]))
    )
    if stage:
        q = q.where(Conversation.stage == stage)
    elif status == "awaiting":
        # 발송 대기 is New, always. Drafts are only ever generated for New tickets, so a
        # waiting draft on any later stage means the ticket moved on — somebody answered
        # it in HubSpot while ours sat here. Showing it asks the operator to send a reply
        # the customer already has. LIST_STAGES said "New only"; it only ever constrained
        # the chip, never the query, so the rows leaked in anyway.
        # **수동 후속 회신은 예외입니다** (2026-08-31). 위 규칙은 「자동 초안은 New 에서만
        # 생기므로 그 뒤 단계에 남은 대기 초안은 이미 늦은 것」이라는 뜻인데, 운영자가
        # 협상 중인 티켓에 직접 쓴 회신은 늦은 것이 아니라 지금 하는 일입니다. 걸러 내면
        # 쓰다 만 초안을 다시 찾을 길이 그 티켓 화면밖에 없습니다.
        q = q.where(
            (Conversation.stage.in_(LIST_STAGES["awaiting"]))
            | (Message.prompt_variant == MANUAL_REPLY_VARIANT)
        )
    # Sort by the column the 접수 시간 cell actually shows, not by our draft's
    # created_at — otherwise "오래된 순" produces a visibly unsorted date column.
    order_column = (
        Conversation.last_incoming_at if stage == "negotiation" else Conversation.created_at
    )
    q = q.order_by(order_column.asc() if sort == "oldest" else order_column.desc()).limit(100)

    with SessionLocal() as session:
        rows = session.execute(q).all()
        messages = [
            {
                "id": msg.id,
                "status": msg.status,
                "stage": conv_stage if conv_stage in VALID_PIPELINE_STAGES else "new",
                # The customer's own subject, shown exactly as HubSpot holds it. The
                # fallback is our REPLY subject (drafting/draft_failed rows can predate
                # the ticket subject), and that one is built as "RE: <original>" — this
                # column is 문의 제목, so the prefix we added comes back off. A "RE:"
                # the CUSTOMER wrote is part of their subject and stays.
                "subject": inquiry_subject or strip_reply_prefixes(msg.subject) or "(제목 없음)",
                # 채널 자리에 있던 값입니다. 전부 "email" 이라 아무 줄도 구분하지 못했고,
                # 그 폭이 정작 궁금한 것 — 이게 무슨 문의인가 — 을 가리고 있었습니다.
                "category": inquiry_category,
                "email": email or "-",
                # New chip → when the ticket arrived; Negotiating → when they last
                # wrote back. Both already on the row, no extra query.
                "received_at": (
                    last_incoming_at if stage == "negotiation" and last_incoming_at else conv_created
                ),
                # Priority is measured from the customer's last message, not from our
                # draft: it answers "how long have they been waiting?".
                "waiting_since": last_incoming_at or conv_created,
            }
            for (
                msg, conv_stage, inquiry_subject, inquiry_category,
                conv_created, last_incoming_at, email,
            ) in rows
        ]
    return {
        "messages": messages,
        "filter_status": status,
        "filter_stage": stage,
        "filter_sort": sort,
        # Built here, not in the template: the labels come from PIPELINE_STAGES, which is
        # also what fixes their order and keeps a renamed stage from needing two edits.
        #
        # Empty when the bucket holds a single stage. 발송 대기 is New-only since the
        # Negotiating chip was dropped, so its row had become 전체 and New — two chips
        # selecting the same rows, and a filter that cannot filter is worse than none.
        "stage_chips": (
            [("", "전체")]
            + [(key, label) for key, label, _ in PIPELINE_STAGES if key in LIST_STAGES[status]]
            if len(LIST_STAGES[status]) > 1
            else []
        ),
        # Same label map as the board and the dashboard — the column must read
        # "New"/"Negotiating", not the raw stage key.
        "stage_labels": {key: label for key, label, _ in PIPELINE_STAGES},
        # 유형 이름과 "세일즈 리드인가" 는 서버가 정합니다 — 분류기가 내는 키와 화면에
        # 보이는 이름이 두 곳에 있으면 새 유형이 빈칸으로 나타납니다.
        "category_labels": CATEGORY_LABELS,
        "unqualified": sorted(UNQUALIFIED),
        "now": list_now().replace(tzinfo=None),
    }


@router.post("/messages/{message_id}/translate")
async def message_translate(
    message_id: int,
    body: str = Form(""),
    subject: str = Form(""),
    signature_key: str = Form(""),
):
    """Put the draft into the inquiry's language when it is not already there.

    **초안은 이제 처음부터 나갈 언어로 쓰입니다**, 그래서 이 버튼은 대개 화면에 없습니다.
    남아 있는 쓰임은 운영자가 본문을 한국어로 고쳐 놓았을 때 하나입니다 — 그때는 고친
    본문을 목표 언어로 옮기고, 옮기기 전의 한국어를 ``body_ko`` 에 남깁니다.

    **The subject is not translated here — it is already in the inquiry's language.**
    Both of the ways a subject is produced settle that at draft time: "RE: <original>"
    reuses the customer's own words (translating those would break the mail client's
    subject threading), and a policy document's fixed subject goes through
    ``inbound._subject_in_inquiry_language``. Translating here instead would round-trip
    an operator-written English subject through Korean and back.

    Returns JSON {subject, body, language, translated} which the page swaps into
    the editable fields.
    """
    cur_body = body.strip()
    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        if not msg:
            return JSONResponse({"error": "메시지를 찾을 수 없습니다"}, status_code=404)
        if msg.status != "pending_approval":
            return JSONResponse({"error": f"번역 불가 (현재 상태: {msg.status})"}, status_code=400)
        conv_id = msg.conversation_id
        target = (msg.target_language or "").lower()
        cur_body = cur_body or msg.body or ""
        cur_subject = subject.strip() or (msg.subject or "")

        # 서명 선택만 저장합니다. 본문에서 서명을 떼어내던 코드가 여기 있었는데, 이제
        # 모델이 본문에 서명을 쓰지 않으므로 뗄 것이 없습니다(0061). 서명은 발송할 때
        # 본문 아래로 붙습니다.
        msg.signature_key = _clean_signature_key(signature_key)

        # Decide from the BODY's actual language, not the (possibly stale) msg.language
        # flag — so re-editing the draft back to Korean and pressing 번역하기 again
        # actually re-translates. A cheap script check, no LLM: a Korean body and a
        # non-Korean target means there is something to translate.
        needs_tx = bool(target) and target != "ko" and is_mostly_korean(cur_body)
        if not needs_tx:
            washed = text_wash(cur_body)
            msg.body = washed
            korean_draft = msg.body_ko
            if cur_subject:
                msg.subject = cur_subject
            # Keep metadata honest: a non-Korean body for a non-Korean target is
            # already in the send language. Stamping the target onto a KOREAN body is
            # what made a Korean draft claim to be English.
            if target and target != "ko" and not is_mostly_korean(washed):
                msg.language = target
            session.commit()
            return JSONResponse(
                {
                    "body": washed,
                    "subject": cur_subject,
                    "language": msg.language,
                    "translated": False,
                    "body_ko": korean_draft,
                }
            )

        translated = await asyncio.to_thread(translate_to, cur_body, target)
        final_body = text_wash(translated) if translated else text_wash(cur_body)
        # 번역이 덮어쓰기 전의 한국어. **이 칸이 없던 동안 운영자는 「무엇을 승인했는지」를
        # 다시 읽을 방법이 없었습니다** — 번역이 뜻을 바꿨는지 확인하려면 원문이 있어야
        # 하고, 번역은 이 화면에서 한 번 누르면 되돌릴 수 없습니다. `body_ko` 는 「이 메일의
        # 한국어 판본」이라는 뜻이고, 그건 고객 문의든 우리 초안이든 같습니다 (0045).
        korean_draft = text_wash(cur_body) if translated else msg.body_ko
        msg.body = final_body
        if cur_subject:
            msg.subject = cur_subject
        if translated:
            msg.language = target
            msg.body_ko = korean_draft
        session.commit()

    if translated:
        add_progress(conv_id, "translate", f"회신 초안을 '{target}' 언어로 번역함.")
    return JSONResponse(
        {
            "body": final_body,
            "subject": cur_subject,
            "language": target if translated else "ko",
            "translated": bool(translated),
            "body_ko": korean_draft,
        }
    )


@router.post("/messages/{message_id}/send")
async def message_send(
    request: Request,
    message_id: int,
    body: str = Form(""),
    subject: str = Form(""),
    signature_key: str = Form(""),
):
    """Approve (and optionally edit) a message, then send it immediately.

    Human approval IS the decision to send, so we dispatch inline here rather than
    leaving the message in 'approved' for the background send worker — a paused or
    absent worker must never strand an already-approved reply.
    """
    if len(body.encode("utf-8")) > _MAX_EDIT_BODY_BYTES:
        return HTMLResponse("<div class='text-red-600 text-sm'>본문이 너무 깁니다.</div>", status_code=400)
    clean_subject = subject.strip()
    clean_body = body.strip()
    if not clean_subject or not clean_body:
        return HTMLResponse(
            "<div class='text-red-600 text-sm'>제목과 본문을 모두 입력해야 발송할 수 있습니다.</div>",
            status_code=400,
        )
    if len(clean_subject) > _MAX_EDIT_SUBJECT_LEN:
        return HTMLResponse("<div class='text-red-600 text-sm'>제목이 너무 깁니다.</div>", status_code=400)

    try:
        approve(
            message_id,
            approver=actor_name(request, fallback="web_ui"),
            edited_body=clean_body,
            edited_subject=clean_subject,
            signature_key=_clean_signature_key(signature_key),
        )
    except ApprovalError as exc:
        return HTMLResponse(
            f'<div class="text-red-600 text-sm">{esc(str(exc))}</div>', status_code=400
        )

    # When the background send worker is running, let IT claim & send this approved
    # row — sending inline here too would let both paths dispatch the same email
    # (the worker claims status='approved'). When the worker is OFF we send inline
    # below so a paused/absent worker never strands an already-approved reply.
    if settings.SEND_WORKER_ENABLED:
        return HTMLResponse(
            '<div class="text-green-600 text-sm font-medium">승인 완료 — 백그라운드 발송 대기 중</div>'
        )

    from ...agents.send_worker import send_approved_now

    if not await send_approved_now(message_id):
        return HTMLResponse(
            '<div class="text-red-600 text-sm font-medium">승인됐지만 발송에 실패했습니다 — 잠시 후 다시 시도해 주세요</div>',
            status_code=500,
        )

    return HTMLResponse('<div class="text-green-600 text-sm font-medium">승인 및 발송 완료</div>')


@router.post("/messages/preview")
async def message_preview(body: str = Form(""), signature_key: str = Form("")):
    """Render a draft body as the HTML email it will become — live approval preview.

    Stateless: takes the (possibly edited) textarea content + the chosen signature
    and returns the same styled HTML the send path attaches, so the approver sees
    the real look.
    """
    from ...integrations.email_html import branded_signature_html, to_html_email

    key = _clean_signature_key(signature_key)
    return HTMLResponse(to_html_email(body, signature_html=branded_signature_html(key)))


@router.post("/messages/{message_id}/reject")
async def message_reject(request: Request, message_id: int, reason: str = Form("")):
    """Reject a message with an optional reason."""
    try:
        reject(
            message_id,
            approver=actor_name(request, fallback="web_ui"),
            reason=reason.strip() or None,
        )
    except ApprovalError as exc:
        return HTMLResponse(
            f'<div class="text-red-600 text-sm">{esc(str(exc))}</div>', status_code=400
        )
    return HTMLResponse('<div class="text-orange-600 text-sm font-medium">거절 처리 완료</div>')


@router.post("/messages/{message_id}/redraft")
async def message_redraft(message_id: int):
    """초안을 처음부터 다시 씁니다 — 새 행을 만들지 않고 이 메시지를 덮어씁니다.

    발송이 실패했을 때 할 수 있는 일은 둘입니다. 같은 글을 그대로 다시 보내거나(「검토 완료 ·
    발송」), 글부터 다시 쓰거나(여기). 후자가 필요한 이유는 실패가 배달 사고만이 아니기
    때문입니다 — 오늘 실패한 초안들은 옛 코드가 쓴 것이라 제목이 영어이고 미팅 링크가 맺음말
    아래에 있었습니다. 그런 초안은 다시 보내도 같은 것이 나갑니다.

    일은 ``inbound_worker.request_redraft`` 가 합니다. 복구 화면의 「재시도」도 같은 함수를
    부릅니다 — 화면이 둘이지 동작이 둘이 아닙니다.
    """
    from ...agents.inbound_worker import RedraftError, request_redraft

    try:
        await run_in_threadpool(request_redraft, message_id)
    except RedraftError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return JSONResponse({"status": "drafting"})


@router.post("/messages/{message_id}/edit")
async def message_edit(
    message_id: int,
    body: str = Form(""),
    subject: str = Form(""),
    signature_key: str = Form(""),
):
    """Save edits to a pending message without sending (body, subject, signature)."""
    if len(body.encode("utf-8")) > _MAX_EDIT_BODY_BYTES:
        return HTMLResponse(
            '<div class="text-red-600 text-sm">본문이 너무 깁니다 (100KB 초과)</div>',
            status_code=413,
        )
    if len(subject) > _MAX_EDIT_SUBJECT_LEN:
        return HTMLResponse(
            '<div class="text-red-600 text-sm">제목이 너무 깁니다 (300자 초과)</div>',
            status_code=413,
        )
    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        if not msg:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">메시지를 찾을 수 없습니다</div>',
                status_code=404,
            )
        if msg.status != "pending_approval":
            return HTMLResponse(
                f'<div class="text-red-600 text-sm">편집 불가 (현재 상태: {esc(msg.status)})</div>',
                status_code=400,
            )
        if body.strip():
            msg.body = body.strip()
        if subject.strip():
            msg.subject = subject.strip()
        msg.signature_key = _clean_signature_key(signature_key)
        session.commit()
    return HTMLResponse('<div class="text-blue-600 text-sm font-medium">저장 완료</div>')


_MAX_ROLE_DESC_LEN = 4000


# 운영자가 직접 쓴 후속 회신. 자동 초안과 한 표에 살지만 출처가 다르고, 그 차이를 이 한
# 글자가 나릅니다 — `stage_sync` 의 초안 청소가 이것만 비켜 가고, 아래 목록이 New 가 아닌
# 티켓의 이것만 통과시킵니다.
MANUAL_REPLY_VARIANT = "manual"

# 아직 나가지 않은 회신. 하나라도 열려 있으면 새로 만들지 않고 그것을 엽니다 — 같은 티켓에
# 초안이 둘이면 어느 것이 나갈지 화면만 봐서는 알 수 없습니다.
_OPEN_DRAFT_STATUSES = ("drafting", "pending_approval", "approved", "send_failed")


@router.post("/tickets/{conversation_id}/reply")
async def start_manual_reply(
    conversation_id: int,
    subject: str = Form(""),
    body: str = Form(""),
):
    """후속 회신 한 통을 **모델 없이** 만듭니다 — 본문은 운영자가 씁니다.

    자동 초안은 New 티켓에만 생기고(`inbound.handle` 의 `skipped_not_new`), 한 번 회신이
    나간 대화에는 다시 생기지 않습니다(`skipped_reply_exists`). 그래서 그 뒤의 대화는 전부
    허브스팟에서 사람이 했고, 우리 화면에는 무엇이 오갔는지가 남지 않았습니다.

    만들어진 뒤는 자동 초안과 **완전히 같은 길**입니다 — 편집·번역·승인·발송·거절 라우트를
    그대로 쓰고, 그래서 발송 관문(언어·수신자·safe mode)도 그대로 걸립니다. 여기서 하는 일은
    빈 초안 한 줄을 세우는 것뿐이라 모델을 부르지 않습니다.
    """
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        if conv is None:
            return JSONResponse({"detail": "티켓을 찾을 수 없습니다"}, status_code=404)
        # 발송 경로가 티켓 스레드 회신 하나뿐이라(CLAUDE.md), 티켓이 없으면 보낼 길이
        # 없습니다. 여기서 막지 않으면 운영자가 다 쓰고 발송을 누른 뒤에야 알게 됩니다.
        if not (conv.hubspot_ticket_id or "").strip():
            return JSONResponse(
                {"detail": "허브스팟 티켓이 없는 문의라 회신을 보낼 길이 없습니다"},
                status_code=400,
            )
        contact = session.get(Contact, conv.contact_id) if conv.contact_id else None
        to_address = (getattr(contact, "email", "") or "").strip()
        if not to_address:
            return JSONResponse(
                {"detail": "이 연락처에는 이메일 주소가 없습니다"}, status_code=400
            )

        open_draft = (
            session.query(Message)
            .filter(
                Message.conversation_id == conv.id,
                Message.direction == "outgoing",
                Message.status.in_(_OPEN_DRAFT_STATUSES),
            )
            .order_by(Message.id.desc())
            .first()
        )
        if open_draft is not None:
            return {"message_id": open_draft.id, "created": False}

        # 나갈 언어는 문의가 정합니다 — 자동 초안과 같은 규칙입니다. `language` 를 같은
        # 값으로 두면, 운영자가 그 언어로 쓰는 한 번역 관문이 뜨지 않고 한국어로 쓰면 뜹니다
        # (`approval.translation_required`).
        target = ((conv.inquiry_language or "ko").strip().lower()) or "ko"
        msg = Message(
            conversation_id=conv.id,
            direction="outgoing",
            channel="email",
            to_address=to_address,
            subject=subject.strip()
            or reply_subject(conv.inquiry_subject, target_code=target),
            body=body.strip(),
            language=target,
            target_language=target,
            status="pending_approval",
            prompt_variant=MANUAL_REPLY_VARIANT,
        )
        session.add(msg)
        session.commit()
        # 진행 기록은 남기지 않습니다 — 초안을 만든 것은 우리 안의 사정이고, 실제로 나가면
        # 발송 경로가 「답변 발송 완료」를 적습니다(`send_worker._post_send_bookkeeping`).
        return {"message_id": msg.id, "created": True}


@router.post("/contacts/history-digest")
async def contact_history_digest(limit: int = 40):
    """이미 가져다 둔 기록에 **한 줄 요약만** 채웁니다 — 허브스팟에 다시 다녀오지 않습니다.

    옛 기록을 끌어올 때는 요약을 안 만들었습니다. 그 값들은 이미 우리 DB 에 본문째로 있고,
    필요한 것은 줄이는 일뿐이라 밖으로 나갈 이유가 없습니다(운영자 지적).

    한 번에 ``limit`` 건씩 하고 남은 수를 돌려줍니다. 다 될 때까지 다시 부르면 됩니다 —
    진행 위치는 따로 기록하지 않습니다. **`context` 가 비어 있다는 것이 곧 「아직 안 했다」**
    이고, 그것이 이어하기입니다.

    이미 값이 있는 행은 건드리지 않습니다. 그 칸은 사람이 적을 수도 있는 자리라, 덮어쓰면
    운영자가 쓴 메모가 모델 한 줄로 바뀝니다.

    짧은 기록은 건너뜁니다(`_one_line` 이 그 판단을 합니다). 세 줄짜리 메모를 한 줄로
    줄여 봐야 같은 말이고, 그 왕복만 늘어납니다.
    """
    from sqlalchemy import func as sa_func

    from .customer_ops import _one_line

    with SessionLocal() as session:
        rows = (
            session.execute(
                select(CustomerInteraction)
                .where(
                    CustomerInteraction.context.is_(None),
                    sa_func.length(CustomerInteraction.summary) >= 80,
                )
                .order_by(CustomerInteraction.happened_at.desc())
                .limit(max(1, min(limit, 200)))
            )
            .scalars()
            .all()
        )
        targets = [(row.id, row.direction, row.subject, row.summary) for row in rows]
        remaining = session.scalar(
            select(sa_func.count(CustomerInteraction.id)).where(
                CustomerInteraction.context.is_(None),
                sa_func.length(CustomerInteraction.summary) >= 80,
            )
        )

    filled = 0
    digests: dict[int, str] = {}
    for row_id, direction, subject, body in targets:
        line = await asyncio.to_thread(_one_line, direction or "", subject, body)
        if line:
            digests[row_id] = line
    if digests:
        with SessionLocal() as session:
            for row_id, line in digests.items():
                row = session.get(CustomerInteraction, row_id)
                if row is not None and row.context is None:
                    row.context = line
                    filled += 1
            session.commit()
    logger.info("기록 요약: %d건 시도, %d건 채움, 남은 %d건", len(targets), filled, remaining or 0)
    return JSONResponse(
        {"processed": len(targets), "filled": filled, "remaining": max(0, (remaining or 0) - len(targets))}
    )


def _write_local_plan_fields(
    contact_id: int, incoming: dict[str, str], sheet_client_id: int | None
) -> None:
    """폼이 보낸 플랜 칸을 우리 프로필과 워크북에 씁니다.

    ``contact_sync.apply_contact_fields`` 를 쓰지 않는 이유는 **빈 칸의 뜻이 반대**라서입니다.
    저쪽에서 흘러들어오는 값의 빈 칸은 「허브스팟이 아직 모른다」라 덮으면 안 되고, 이 폼의
    빈 칸은 **사람이 일부러 비운 것**이라 지워야 합니다 — 잘못 들어간 값을 되돌릴 길이 있어야
    합니다. 두 뜻을 한 함수에 담으면 어느 쪽 호출자를 위한 규칙인지가 그 안에서 사라집니다.

    워크북은 자리가 있는 칸만 갑니다. 수식 칸은 `SYNCABLE_INBOUND_FIELDS` 가 막습니다 —
    특히 Pipeline(MQL/PQL)은 구독 플랜에서 저절로 계산되므로 플랜만 쓰면 따라옵니다.

    시트가 실패해도 저장은 성공입니다. 그것이 방금 허브스팟에 들어간 값을 되돌릴 이유는
    아니고, 이유는 로그에 남습니다.
    """
    from ...agents.contact_sync import FIELDS, SHEET_FIELDS

    with SessionLocal() as session:
        profile = session.get(CustomerProfile, contact_id) or CustomerProfile(
            contact_id=contact_id
        )
        touched: dict[str, str] = {}
        for prop, value in incoming.items():
            column = FIELDS[prop]
            setattr(profile, column, value or None)
            touched[column] = value
        session.add(profile)
        session.commit()

    sheet_values = {
        key: touched[column] for column, key in SHEET_FIELDS.items() if column in touched
    }
    if sheet_values and sheet_client_id:
        from ...integrations.google_sheets import update_inbound_fields

        update_inbound_fields(sheet_client_id, sheet_values)


@router.post("/contacts/{contact_id}/hubspot-record")
async def contact_hubspot_record_edit(contact_id: int, request: Request):
    """티켓 세부 내역의 「플랜 정보」를 허브스팟 연락처에 되쓴다.

    이 화면에서 유일하게 **허브스팟에 쓰는** 폼이다. 제품 쪽 연동이 100% 가 아니라 사람이
    채워야 할 때가 있다는 운영자 판단으로 열었다.

    막는 자리는 여기가 아니라 `update_record_fields` 안이다 — 안전 모드 확인을 라우트에
    두면 다음 호출자(폴러·배치)가 그 앞을 안 지난다. 라우트가 하는 일은 그 예외를 화면이
    읽을 수 있는 말로 바꾸는 것뿐이다.

    폼 키는 우리 `Field.key`(`user_seq` …)이고 허브스팟 속성 이름은 서버가 다시 찾는다.
    브라우저가 보낸 이름을 그대로 쓰면 콘솔에 닿은 누구든 남의 속성을 덮어쓸 수 있다.
    """
    from ...common.safe_mode import ExternalWriteBlocked
    from ...integrations.hubspot_record import update_record_fields

    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            return JSONResponse({"error": "연락처를 찾을 수 없습니다"}, status_code=404)
        hubspot_contact_id = contact.hubspot_contact_id
        sheet_client_id = contact.sheet_client_id
    if not hubspot_contact_id:
        return JSONResponse(
            {"error": "이 고객은 허브스팟 연락처가 아니라 저장할 곳이 없습니다"},
            status_code=400,
        )

    form = await request.form()
    values = {key: str(value) for key, value in form.items()}
    try:
        await asyncio.to_thread(update_record_fields, hubspot_contact_id, values)
    except ExternalWriteBlocked:
        return JSONResponse(
            {"error": "안전 모드라 허브스팟에 쓰지 않았습니다"}, status_code=409
        )
    except Exception as exc:  # noqa: BLE001 - 화면에 이유를 적어 주려고 넓게 잡는다
        logger.warning("HubSpot record write failed for contact %s: %s", contact_id, exc)
        return JSONResponse({"error": "허브스팟에 저장하지 못했습니다"}, status_code=502)

    # **저장하면 저장해야 할 곳에 다 저장합니다** (2026-08-19 운영자 지시). 다섯 칸 전부
    # 우리 프로필에 자리가 있고(0094) 화면이 읽는 곳도 이제 거기이므로, 허브스팟에만 쓰면
    # 방금 저장한 값이 다음 스윕까지 화면에 안 보입니다.
    from ...agents.contact_sync import FIELDS

    incoming = {prop: values[prop].strip() for prop in FIELDS if prop in values}
    if incoming:
        await run_in_threadpool(_write_local_plan_fields, contact_id, incoming, sheet_client_id)
    return JSONResponse({"ok": True})


@router.post("/contacts/{contact_id}/edit")
async def contact_edit(contact_id: int, company: str = Form(""), role_description: str = Form("")):
    """연락처 저장 — **그 값이 사는 곳 전부에** 씁니다 (2026-08-19 운영자 지시).

    예전에는 우리 DB 한 곳이었습니다. 같은 회사 이름이 허브스팟과 워크북에는 옛 값으로
    남아, 세 화면이 같은 사람을 다른 회사로 부르는 상태가 됐습니다.

    회사 이름이 사는 곳은 셋입니다:

      * `contacts.company` — 콘솔이 읽는 값
      * 허브스팟 연락처의 `company` 속성 — 영업이 저쪽에서 보는 값
      * 워크북 「고객 기본 정보」의 고객사 열 — Inbound DB 가 Client ID 로 조회하는 원본
        (그래서 그 탭 한 곳만 고치면 문의 행이 따라옵니다)

    「무엇을 하는 회사인가」(`role_description`)는 우리에게만 있는 칸이라 DB 뿐입니다.

    **바깥 두 곳이 실패해도 저장은 성공입니다.** 운영자가 방금 친 글자를 잃는 것보다
    나중에 다시 맞추는 편이 낫고, 실패는 로그와 응답에 남습니다. 안전 모드에서는 바깥
    쓰기가 애초에 막히므로(`guard_external_write`) 로컬만 바뀝니다.

    Works even for gmail/unverified senders — the operator fills these in over the
    course of a conversation, and they persist to the DB.
    """
    if len(role_description) > _MAX_ROLE_DESC_LEN:
        return HTMLResponse(
            '<div class="text-red-600 text-sm">설명이 너무 깁니다 (4000자 초과)</div>',
            status_code=413,
        )
    with SessionLocal() as session:
        c = session.get(Contact, contact_id)
        if not c:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">연락처를 찾을 수 없습니다</div>',
                status_code=404,
            )
        c.company = company.strip() or None
        c.role_description = role_description.strip() or None
        hubspot_contact_id = c.hubspot_contact_id
        # 워크북 행을 찾는 자연키. 문의에 붙은 값이 먼저고, 없으면 연락처에 박힌 값입니다 —
        # 단계 동기화가 쓰는 것과 같은 순서입니다.
        client_id = c.sheet_client_id or next(
            (conv.sheet_client_id for conv in c.conversations if conv.sheet_client_id), None
        )
        session.commit()

    saved_company = company.strip()
    elsewhere: list[str] = []
    if saved_company and hubspot_contact_id:
        try:
            from ...integrations.hubspot import HubSpotClient

            await asyncio.to_thread(
                HubSpotClient().update_contact_company_sync, hubspot_contact_id, saved_company
            )
            elsewhere.append("허브스팟")
        except Exception as exc:  # noqa: BLE001 - 바깥이 실패해도 저장은 끝났습니다
            logger.warning("허브스팟 회사 이름 저장 실패 (contact=%s): %s", contact_id, exc)
    if saved_company and client_id:
        try:
            from ...integrations.google_sheets import update_registry_company

            if await asyncio.to_thread(update_registry_company, client_id, saved_company):
                elsewhere.append("워크북")
        except Exception as exc:  # noqa: BLE001
            logger.warning("워크북 회사 이름 저장 실패 (client_id=%s): %s", client_id, exc)

    where = " · ".join(["콘솔", *elsewhere])
    return HTMLResponse(
        f'<div class="text-green-600 text-sm font-medium">저장 완료 ({where})</div>'
    )
