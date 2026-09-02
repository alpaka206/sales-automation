"""JSON for the React screens.

**Plain ``def``, not ``async def``.** These do blocking SQLAlchemy work and nothing else,
and FastAPI runs a sync endpoint in its threadpool while an ``async`` one runs ON the event
loop — so an ``async def`` that never awaits blocks every other request for the duration of
its queries. They were all ``async def``; a single ticket open (~11 sequential round trips)
held up the SSE stream and the queue poll behind it.

Deliberately thin. Every one of these calls the SAME context builder the Jinja template
renders, so a screen's data has one definition and the two front ends cannot disagree
about what a row contains. When a screen is ported, its Jinja template goes; the builder
behind it stays exactly where it is.

Browser-authenticated, not token-authenticated: the SPA is served from this origin and
carries the operator's session cookie, so ``/api/ui`` is registered in
``security.WEB_UI_PREFIXES``. Writes are NOT here — the React screens post to the same
routes the forms do, so the send guard, stage sync and safe-mode block stay in one place.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...common.sheet_values import qualification_for_plan
from ...db.models import Contact, Conversation

router = APIRouter(tags=["ui-api"])


# ---------------------------------------------------------------------------------- #
# Live updates
# ---------------------------------------------------------------------------------- #
# What React on its own does NOT give you: a change made on one screen showing up on
# another screen, in another tab, in front of another person. React state lives in one
# tab; only the server knows something happened. So writes publish a one-word topic here
# and every open console re-reads what it is showing.
#
# In-process on purpose: one uvicorn worker serves this console. With several workers a
# subscriber only hears writes handled by its own — the fix then is Redis pub/sub, not a
# bigger version of this.
# ponytail: in-process fan-out, swap for Redis pub/sub if this ever runs multi-worker.
_subscribers: set[asyncio.Queue[str]] = set()


def publish(topic: str) -> None:
    """Tell every open console that ``topic`` changed. Never raises, never blocks."""
    for queue in list(_subscribers):
        try:
            queue.put_nowait(topic)
        except asyncio.QueueFull:  # pragma: no cover - a dead tab, not a failure
            _subscribers.discard(queue)


@router.get("/api/ui/events")
async def ui_events(request: Request) -> StreamingResponse:
    """Server-sent events: one line per change, plus a keepalive so proxies hold on."""

    async def stream():
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        _subscribers.add(queue)
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    topic = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"data: {topic}\n\n"
                except TimeoutError:
                    # A comment, not an event: keeps the connection open without
                    # telling the client anything changed.
                    yield ": keepalive\n\n"
        finally:
            _subscribers.discard(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _card(row: dict) -> dict:
    """A board row's ORM objects flattened to what a card actually draws."""
    from .customer_ops import visible_deal_detail

    conversation: Conversation = row["conversation"]
    contact: Contact = row["contact"]
    return {
        "conversation_id": conversation.id,
        "ticket_id": conversation.hubspot_ticket_id,
        # The ticket's name — the customer's own subject line. It is what the card is
        # titled with: one company files several inquiries, and the company name is
        # the same on all of them. Chosen in _pipeline_rows, beside the message it
        # can fall back to.
        "subject": row["subject"],
        "contact_id": contact.id,
        "company": contact.company,
        "name": contact.full_name,
        "email": contact.email,
        "country": contact.country,
        "client_id": row["client_id"],
        "last_activity": row["last_activity"],
        "stage": row["stage"],
        # Won Type / Lost Reason. 티켓 세부 내역과 같은 판단을 같은 곳에서 합니다.
        "deal_detail": visible_deal_detail(row["stage"], conversation.deal_detail),
    }


@router.get("/api/ui/dashboard")
def ui_dashboard(_request: Request):
    from .customer_ops import DEAL_DETAILS
    from .dashboard import _dashboard_context

    context = _dashboard_context()
    return {
        # 어느 열에 Deal Detail 고르개가 붙는지, 거기 무엇을 고를 수 있는지. 서버가 주므로
        # 값 목록이 화면과 검증 두 곳에 따로 적히지 않습니다 — 라우트가 거절하는 값이
        # 고르개에 들어 있는 상태가 생기지 않습니다.
        "deal_details": {stage: list(values) for stage, values in DEAL_DETAILS.items()},
        "queue": context["recent_messages"],
        "now": context["now"],
        "counters": {
            "received_today": context["received_today"],
            "awaiting_total": context["awaiting_total"],
        },
        "stage_labels": context["stage_labels"],
        "category_labels": context["category_labels"],
        "unqualified": context["unqualified"],
        "manual_log_stages": list(context["manual_log_stages"]),
        "stages": [
            {
                "key": stage["key"],
                "label": stage["label"],
                "total": stage["total"],
                "cards": [_card(row) for row in stage["rows"]],
            }
            for stage in context["stages"]
        ],
    }



# 「계약 장부」(`GET /api/ui/contracts`)가 여기 있었습니다. **부르는 화면이 하나도
# 없었습니다** — 수주 고객이 `clients`/`client_contracts` 로 옮겨 가면서 화면만 갈아탔고,
# 이 엔드포인트와 그 뒤의 `_contract_rows`/`_contract_summary` 가 `contract_records` 를 읽은
# 채로 남았습니다. 그 표는 그대로입니다(고객 상세의 「계약 · 결제」 폼이 씁니다) — 지운 것은
# 아무도 안 부르는 통로입니다 (2026-08-27 운영자 지시).


@router.get("/api/ui/messages")
def ui_messages(status: str = "awaiting", stage: str = "", sort: str = "oldest"):
    """회신 및 검토. Returned as built — the context is already plain dicts, and every
    filter value is allow-listed inside the builder, so nothing is validated twice."""
    from .messages import _messages_list_context

    return _messages_list_context(status=status, stage=stage, sort=sort)


@router.get("/api/ui/messages/{message_id}")
async def ui_message_detail(message_id: int):
    """티켓 세부 내역. 읽기만 합니다 — 이 경로에는 모델 호출이 없습니다.

    한국어 번역은 접수할 때 이미 행에 들어가 있습니다(`inbound.cache_korean_inquiries`).
    여기서 번역하던 코드가 있었는데, 그러면 그 티켓을 처음 여는 사람이 매번 Gemini 를
    기다렸다가 화면을 봤습니다. 아직 안 채워진 옛 행은 화면이 원문을 그대로 보여 줍니다.
    """
    from .messages import _message_detail_context

    # In a thread, not on the event loop. One open costs ~11 sequential round trips to
    # Postgres, and on the loop every other request — the SSE stream, the 15-second queue
    # poll, another operator's screen — waits behind them. That is what "살짝 늦게 뜬다"
    # was. It does not make this call faster; it stops it from slowing everything else.
    context = await asyncio.to_thread(_message_detail_context, message_id)
    return _ticket_screen(context, "메시지를 찾을 수 없습니다")


@router.get("/api/ui/tickets/{conversation_id}")
async def ui_ticket_detail(conversation_id: int):
    """같은 화면을 **대화(티켓) 기준**으로 엽니다 — 보드 카드가 이 길로 들어옵니다.

    메일이 하나도 없는 티켓이 있습니다: ``hubspot_backfill`` 은 티켓에서 대화만 만들고 메일
    행은 만들지 않아서, HubSpot 에서 들여온 Won·Lost 티켓이 전부 그렇습니다. 그 카드를
    누르면 예전에는 고객 페이지로 빠졌는데, Deal Detail 은 **티켓의 값**이라 정작 그것을
    고칠 화면이 없었습니다. 여기로 오면 메일이 없어도 티켓 정보와 Deal Detail 과 소통
    기록은 그대로 있습니다.
    """
    from .messages import _message_detail_context

    context = await asyncio.to_thread(
        _message_detail_context, None, conversation_id=conversation_id
    )
    return _ticket_screen(context, "티켓을 찾을 수 없습니다")


def _ticket_screen(context: dict, not_found: str) -> dict:
    """두 진입점이 함께 얹는 것 — 단계 이름·소통 히스토리 가능 단계·Deal Detail 목록."""
    from .customer_ops import DEAL_DETAILS, MANUAL_LOG_STAGES, PIPELINE_STAGES

    if not context:
        raise HTTPException(status_code=404, detail=not_found)
    context["stage_labels"] = {key: label for key, label, _ in PIPELINE_STAGES}
    # 어느 단계에서 소통 히스토리를 남길 수 있는지. 보드의 + 버튼이 쓰는 것과 같은 목록을
    # 같은 곳에서 보냅니다 — 화면마다 "New 는 빼고" 를 따로 적으면 언젠가 어긋납니다.
    context["manual_log_stages"] = list(MANUAL_LOG_STAGES)
    # 보드 카드가 쓰는 것과 **같은 목록**입니다. 티켓 세부 내역에서도 Won Type / Lost
    # Reason 을 고칠 수 있어야 하고 — 카드를 찾으러 대시보드로 나갔다 오지 않도록 —
    # 고르개가 두 화면에 있으니 값 목록은 더더욱 한 곳에서 와야 합니다.
    context["deal_details"] = {stage: list(values) for stage, values in DEAL_DETAILS.items()}
    return context


@router.get("/api/ui/customers")
def ui_customers(stage: str = "", q: str = ""):
    """리드 히스토리. Same filtering the page does, applied to the same rows."""
    from .customer_ops import PIPELINE_STAGES, _customer_rows

    query = q.strip().lower()
    rows = _customer_rows()
    if stage:
        rows = [row for row in rows if row["stage"] == stage]
    if query:
        rows = [
            row
            for row in rows
            if query
            in " ".join(
                filter(
                    None,
                    [
                        row["contact"].full_name,
                        row["contact"].email,
                        row["contact"].company,
                        row["contact"].domain,
                    ],
                )
            ).lower()
        ]
    return {
        "rows": [
            {
                "contact_id": row["contact"].id,
                "company": row["contact"].company,
                "name": row["contact"].full_name,
                "email": row["contact"].email,
                "stage": row["stage"],
                "temperature": row["temperature"],
                "next_action": row["next_action"],
                "next_action_at": row["next_action_at"],
                "last_activity": row["last_activity"],
                "conversation_count": row["conversation_count"],
                # 플랜에서 나오는 계산값입니다 — 열이 아닙니다(2026-09-02 운영자 지시).
                "qualification": row["qualification"],
                # 수주 DB·워크북·시트가 전부 이 번호로 엮입니다. 목록에 없으면 같은 고객을
                # 다른 화면에서 회사 이름으로 찾아야 합니다(2026-08-19 운영자 지시).
                "client_id": row["client_id"],
            }
            for row in rows
        ],
        "stage_options": [{"key": key, "label": label} for key, label, _ in PIPELINE_STAGES],
    }


def _lead(row: dict) -> dict:
    """A 리드 히스토리 row flattened for the insight lists."""
    contact = row["contact"]
    return {
        "contact_id": contact.id,
        "company": contact.company,
        "name": contact.full_name,
        "email": contact.email,
        "stage": row["stage"],
        "state": row["state"],
        "temperature": row["temperature"],
        "next_action": row["next_action"],
        "last_activity": row["last_activity"],
    }


@router.get("/api/ui/operations")
def ui_operations():
    """고객 인사이트. The follow-up ladder and the renewal window, from the same builder the
    page renders — these numbers must not have a second definition.

    「리드 추이」의 기간별 집계는 화면과 함께 지웠습니다. ``period`` 인자도 같이 사라졌으니
    옛 주소(`?period=year`)로 와도 그냥 무시됩니다.
    """
    from .customer_ops import _operations_context

    context = _operations_context()
    return {
        "follow_up_days": context["follow_up_days"],
        "lists": {
            key: [_lead(row) for row in context[key]]
            for key in ("stale", "missing_reply", "due_reminder_1", "due_reminder_2",
                        "due_unqualified", "lost", "upsell")
        },
        "renewals": [
            {
                "contact_id": contact.id,
                "company": contact.company or contact.full_name,
                "plan": contract.plan,
                "amount": contract.amount,
                "currency": contract.currency,
                "expires_at": contract.expires_at,
            }
            for contract, contact in context["renewals"]
        ],
    }


@router.get("/api/ui/companies/{domain}")
def ui_company(domain: str):
    """회사 상세. Personal domains are never grouped — the route already refuses, and
    this returns exactly what it decided rather than deciding again."""
    from .companies import company_context

    return company_context(domain)


@router.get("/api/ui/settings/users")
def ui_settings_users(request: Request):
    """접근 승인. Gated by admin_required, the one gate — the same function every other
    admin screen uses, so which module a route imports from stops deciding who gets in.

    이 화면은 한동안 아예 열리지 않았습니다. 여기서 ``settings_page.settings_users`` 를
    불러 그 템플릿 context 를 JSON 으로 바꿔 쓰고 있었는데, 템플릿이 사라질 때 그 함수도
    같이 사라졌습니다 — 남은 import 는 ImportError, 즉 **500** 이었습니다. 그런데 화면은
    실패를 전부 "관리자만 접근할 수 있습니다" 로 그렸기 때문에, 관리자로 로그인한 사람에게
    권한이 없다고 말하는 화면이 됐습니다. 목록을 여기서 직접 만듭니다: 지울 함수도, 맞춰야
    할 context 모양도 없습니다.
    """
    from ...common.config import settings as app_settings
    from ...db.models import User
    from ...db.session import SessionLocal
    from ..auth import admin_required, normalize_role, session_user

    if not admin_required(request):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")

    me = session_user(request) or {}
    with SessionLocal() as session:
        rows = (
            session.query(User)
            .filter(User.approved.is_(True))
            .order_by(User.email)
            .all()
        )
        users = [
            {
                "email": user.email,
                "name": user.name or "",
                # 저장된 값이 아니라 **실제로 적용되는** 권한입니다. legacy 'member' 행은
                # 전체 접근으로 풀리는데, 화면이 'member' 라고 적으면 조회 전용처럼 읽힙니다.
                "role": normalize_role(user.role),
                "approved": bool(user.approved),
                "last_login_at": user.last_login_at,
            }
            for user in rows
        ]
    return {
        "approved_users": users,
        "me_email": me.get("email", ""),
        "domain": (app_settings.ALLOWED_EMAIL_DOMAIN or "").lower().strip(),
    }


@router.get("/api/ui/recovery")
def ui_recovery(request: Request):
    """복구 — the four durable failure lists the operations screen acts on.

    Same gate as the log rows beside it: this is the tab that offers 재시도 buttons.
    """
    from ..auth import admin_required
    from .recovery import recovery_context, recovery_pending_count

    if not admin_required(request):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    context = recovery_context()

    def _message(message) -> dict:
        conversation = message.conversation
        contact = conversation.contact if conversation else None
        return {
            "id": message.id,
            "status": message.status,
            "subject": message.subject,
            "to_address": message.to_address,
            "created_at": message.created_at,
            # 한 행은 둘 중 하나입니다 — 못 보낸 건(send_error)이거나, 보내고 기록만
            # 실패한 건(post_send_sync_error)이거나. 화면은 이 값을 빨간 줄로 그리므로
            # 어느 쪽이든 이유가 배지 옆에 섭니다.
            "error": message.send_error or message.post_send_sync_error,
            "company": (contact.company or contact.full_name) if contact else None,
        }

    return {
        "pending": recovery_pending_count(context),
        "inbound_jobs": [
            {
                "id": job.id,
                # The ticket id lives in the webhook payload, not a column of its own.
                "ticket_id": (job.payload or {}).get("objectId")
                or (job.payload or {}).get("ticket_id"),
                "source": job.source,
                "status": job.status,
                "attempts": job.attempts,
                "last_error": job.last_error,
                "updated_at": job.updated_at,
            }
            for job in context["inbound_jobs"]
        ],
        "messages": [_message(message) for message in context["messages"]],
        "stale_drafts": [_message(message) for message in context["stale_drafts"]],
        "sync_failures": [_message(message) for message in context["sync_failures"]],
    }


@router.get("/api/ui/logs")
def ui_logs(request: Request, view: str = "all"):
    """운영 로그. Gated by the SAME function the page uses, not by a second one that
    happens to look similar — a JSON copy of a screen must not be the way around that
    screen's gate, and it must not refuse what the screen allows either.

    """
    from ..auth import admin_required
    from .logs import _events

    if not admin_required(request):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    return {"rows": _events(view)}


@router.get("/api/ui/messages/{message_id}/senders")
async def ui_reply_senders(message_id: int):
    """그 회신을 **어느 주소에서** 보낼 수 있나 — 검토 화면의 발신 고르개가 읽습니다.

    본문 payload 와 **따로** 가져옵니다. 허브스팟에 물어야 나오는 값이라 같이 담으면
    답을 읽는 일이 이 조회를 기다리게 됩니다(「플랜 정보」 카드와 같은 이유).

    **읽기만 합니다.** `list_reply_senders` 는 GET 만 하고 쓰기 관문을 안 지납니다 —
    그래서 이 라우트를 여는 것만으로 메일이 나갈 길은 없습니다.

    못 가져와도 200 에 빈 목록입니다. 고르개가 안 뜰 뿐 발송은 예전대로 되고(스레드가
    정합니다), 여기서 404 를 내면 화면이 오류를 그리는데 「고를 것이 없다」는 오류가
    아닙니다. 이유는 `error` 에 실어 화면이 적을 수 있게 합니다.
    """
    from ...db.models import Message
    from ...db.session import SessionLocal
    from ...integrations.hubspot import HubSpotClient

    def _target() -> tuple[str, str, str]:
        with SessionLocal() as session:
            msg = session.get(Message, message_id)
            if msg is None:
                return "", "", ""
            conversation = session.get(Conversation, msg.conversation_id)
            ticket = (conversation.hubspot_ticket_id if conversation else "") or ""
            return ticket, (msg.to_address or ""), (msg.channel_account_id or "")

    ticket_id, recipient, chosen = await asyncio.to_thread(_target)
    if not ticket_id or not recipient:
        return {"senders": [], "chosen": chosen, "error": None}
    try:
        senders = await HubSpotClient().list_reply_senders(ticket_id, recipient)
    except Exception as exc:  # 조회 실패가 검토 화면을 막으면 안 됩니다
        return {"senders": [], "chosen": chosen, "error": f"{type(exc).__name__}: {exc}"}
    return {"senders": senders, "chosen": chosen, "error": None}


@router.get("/api/ui/contacts/{contact_id}/hubspot-record")
def ui_hubspot_record(contact_id: int):
    """허브스팟 연락처 레코드의 「기본 그룹」 — 티켓 세부 내역 오른쪽 카드들.

    티켓 본문 payload 와 **따로** 가져온다. 이 값은 허브스팟에 물어야 나오는데, 그걸
    `/api/ui/tickets/{id}` 안에 넣으면 화면이 뜨는 시각이 허브스팟 응답 시간에 묶인다 —
    답을 읽는 일이 플랜 표시를 기다리게 된다. 패널만 늦게 채워지는 편이 낫다.

    허브스팟 연락처 ID 가 없는 고객(손으로 만든 행, 워크북에서 온 행)은 조회할 대상이
    없다. 그때도 200 에 빈 그룹이다 — 404 로 답하면 화면이 오류를 그리는데, 「이 고객은
    허브스팟에 없다」는 오류가 아니다.
    """
    from ...integrations.hubspot_record import fetch_record_groups

    # **우리 행을 읽습니다** (0094). 허브스팟 연락처 ID 가 있든 없든 상관없어졌습니다 —
    # 손으로 만든 행도 워크북에서 온 행도 자기 칸을 갖고, 비어 있으면 비어 있는 채로
    # 그려집니다. 저쪽에서 값이 들어오는 문은 `agents/contact_sync` 의 셋입니다.
    return fetch_record_groups(contact_id)


@router.get("/api/ui/customers/{contact_id}")
def ui_customer_detail(contact_id: int):
    """고객 상세. The builder returns ORM rows; the screen needs their fields."""
    from .customer_ops import _customer_context, won_block

    context = _customer_context(contact_id)
    if context is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
    contact = context["contact"]
    profile = context["profile"]
    return {
        "contact": {
            "id": contact.id,
            "full_name": contact.full_name,
            "email": contact.email,
            "company": contact.company,
            "domain": contact.domain,
            "phone": contact.phone,
            "lifecycle_stage": contact.lifecycle_stage,
            "hubspot_contact_id": contact.hubspot_contact_id,
        },
        "profile": {
            field: getattr(profile, field)
            for field in (
                "customer_state", "pipeline_stage", "lead_temperature",
                "industry", "user_seq", "current_plan", "source", "next_action",
                "next_action_at", "lost_reason", "notes", "last_synced_at",
            )
        }
        if profile
        else None,
        # MQL / PQL 은 프로필 **밖에** 있습니다. 플랜에서 나오는 계산값이라 프로필 행이
        # 없는 연락처에도 답이 있고(산 적이 없으니 MQL), 안에 두면 그 사람만 「-」가
        # 됩니다 — 실제로 그랬습니다. 저장하던 열은 워크북에서 읽어 온 거울이라 콘솔이
        # 채우지 않았고, 그래서 이 화면은 늘 비어 있었습니다(그 열은 이관 0104 가 지웠습니다).
        "qualification": qualification_for_plan(profile.current_plan if profile else None),
        "stage_options": [
            {"key": key, "label": label} for key, label, _ in context["stage_options"]
        ],
        "conversations": [
            {
                "id": conversation.id,
                "created_at": conversation.created_at,
                "inquiry_subject": conversation.inquiry_subject,
                "stage": conversation.stage,
                "sheet_client_id": conversation.sheet_client_id,
            }
            for conversation in context["conversations"]
        ],
        # 수주 DB·워크북·시트가 이 번호로 엮입니다. 한 사람에게 여럿일 수 있습니다.
        "client_ids": context["client_ids"],
        # **티켓 하나가 블록 하나입니다.** 그 안에 그 티켓의 메일과 진행 기록이 들어갑니다 —
        # 예전에는 모든 티켓의 메일이 한 줄로 섞여 어느 건인지 알 수 없었습니다.
        "tickets": [
            {
                "conversation_id": item["conversation"].id,
                "ticket_id": item["conversation"].hubspot_ticket_id,
                "client_id": item["conversation"].sheet_client_id,
                "subject": item["conversation"].inquiry_subject,
                "category": item["conversation"].inquiry_category,
                "language": item["conversation"].inquiry_language,
                "stage": item["conversation"].stage,
                "created_at": item["conversation"].created_at,
                "last_incoming_at": item["conversation"].last_incoming_at,
                "last_outgoing_at": item["conversation"].last_outgoing_at,
                "summary": item["conversation"].summary,
                "messages": [
                    {
                        "id": message.id,
                        "direction": message.direction,
                        "status": message.status,
                        "subject": message.subject,
                        "body": message.body,
                        "happened_at": message.sent_at or message.created_at,
                    }
                    for message in item["messages"]
                ],
                "progress": [
                    {"kind": row.kind, "detail": row.detail, "created_at": row.created_at}
                    for row in item["progress"]
                ],
            }
            for item in context["tickets"]
        ],
        # 수주 고객. 없으면 null 이고, 그러면 화면이 그 블록을 안 그립니다.
        "won": won_block(context["won_client"], context["won_contracts"]),
        # 소통 히스토리 — 사람 단위 기록. 사라진 티켓의 메일도 여기로 옮겨져 있습니다
        # (`hubspot_reconcile._archive_messages`, handler 가 「지난 티켓」).
        "interactions": [
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
            for item in context["interactions"]
        ],
        "contracts": [
            {
                "id": contract.id,
                "plan": contract.plan,
                "status": contract.status,
                "amount": contract.amount,
                "currency": contract.currency,
                "conversation_id": contract.conversation_id,
                "sheet_client_id": contract.sheet_client_id,
                "contract_date": contract.contract_date,
                "payment_due_at": contract.payment_due_at,
                "paid_at": contract.paid_at,
                "expires_at": contract.expires_at,
                "payment_method": contract.payment_method,
                "language_pairs": contract.language_pairs or [],
                "unit_price": contract.unit_price,
                "quote_url": contract.quote_url,
                "invoice_url": contract.invoice_url,
                "payment_url": contract.payment_url,
                "notes": contract.notes,
            }
            for contract in context["contracts"]
        ],
        "timeline": context["timeline"],
        "same_company": [
            {"id": person.id, "full_name": person.full_name, "email": person.email}
            for person in context["same_company"]
        ],
    }


# The two kinds of row in email_templates, told apart by the only thing that decides how
# a row is used: its key. `signature_*` is what the compose screen's picker offers;
# everything else is a body the send path fetches by exact name (for example the reply
# format). Flat, the list mixed the two and gave no clue which was which.
TEMPLATE_KINDS: tuple[tuple[str, str], ...] = (
    ("signature", "서명"),
    ("template", "이메일 템플릿"),
    # Read-only, and it lives here because an operator asking "what does the reply say?"
    # is asking about all three. Policy is owned in Notion and pulled in by
    # a copy pasted from Notion; nothing overwrites it automatically any more.
    ("policy", "정책 문서"),
)


def _template_kind(key: str) -> str:
    from ...db.email_templates import SIGNATURE_KEY_PREFIX

    return "signature" if key.startswith(SIGNATURE_KEY_PREFIX) else "template"


@router.get("/api/ui/email-templates")
def ui_email_templates():
    """Grouped by kind, and **one entry per row** inside a kind.

    Language-specific rows used to collapse into a single entry with the language picked
    inside it. The send path resolves them by exact key, and the ``_en`` row is the ONLY
    inquiry reads: an operator who edited 답변 메일 형식 edited ``reply_format`` and
    English replies went on using the untouched ``reply_format_en``, with nothing on
    screen to say so. The count here was the tell — it counted rows (11) while the list
    drew groups (6).

    Bodies ride along. There are a handful of rows and the largest is a 1.1 KB signature,
    while a second request costs a full round trip — measured at 200-370 ms from Seoul to
    this service, and that is the FLOOR: /healthz, which touches Postgres, takes the same
    as a static file, so the distance is the cost, not the query. Sending ~10 KB once
    means opening a template is instant instead of "a form appears, then changes".
    """
    from ...db.email_templates import is_code_resolved
    from ...db.models import EmailTemplate
    from ...db.session import SessionLocal

    from ...db.models import PolicySource

    # 지운 행은 여기 안 옵니다 — 지우면 행 자체가 사라지고, 그때 내용은 판본 이력에
    # 남습니다 (0100).
    with SessionLocal() as session:
        policy_count = (
            session.query(PolicySource).count()
        )
        rows = (
            session.query(EmailTemplate)
            .filter(~EmailTemplate.key.like("auto_ack%"))
            .order_by(EmailTemplate.updated_at.desc())
            .all()
        )
        items = [
            {
                "id": row.id,
                # The key, because two of these rows hold nothing but a URL and the screen
                # has to know that to stop asking for a language and an HTML preview.
                "key": row.key,
                "name": row.name,
                "language": row.language or "all",
                "updated_at": row.updated_at,
                # 몇 번째 판인가. 「판본 기록」이 이 번호까지의 이력을 보여 줍니다.
                "version": row.version or 1,
                # 마지막으로 저장한 사람 (0100).
                "author": row.author,
                "kind": _template_kind(row.key),
                # 발송 경로가 이 이름으로 찾는 행인가. 아무 키나 만들고 무엇이든 지울 수
                # 있게 한 뒤로(2026-08-18), 이것이 "이 행은 실제로 쓰인다" 와 "목록에만
                # 있다" 를 가르는 유일한 표시입니다.
                "code_resolved": is_code_resolved(row.key),
                "body": row.body or "",
                "chars": len(row.body or ""),
                # Which signature a new draft starts with. A row, not a literal in
                # inbound.py — and the screen is where it moves.
            }
            for row in rows
        ]
    return {
        "kinds": [
            {
                "key": key,
                "label": label,
                # 지운 행은 `items` 에 애초에 안 실립니다(위 필터) — 세는 쪽에서 다시
                # 거를 것이 없습니다. `item["deleted"]` 를 읽던 줄이 여기 있었고, 그 키를
                # payload 에서 뺀 날 이 화면이 통째로 500 이 됐습니다 (2026-08-27).
                "count": policy_count if key == "policy"
                else sum(1 for item in items if item["kind"] == key),
                # 서명이든 아니든 만들 수 있습니다 (2026-08-18). 만든 행을 읽는 코드가
                # 있는지는 `code_resolved` 가 행마다 말합니다 — 막는 대신 보이게 합니다.
                "can_create": key != "policy",
                "read_only": key == "policy",
            }
            for key, label in TEMPLATE_KINDS
        ],
        "items": items,
    }



@router.get("/api/ui/policy-docs")
def ui_policy_docs():
    """정책 문서 — 등록부 + 사본 + 그 문서를 어떤 문의에 쓸지.

    한동안 읽기 전용이었습니다: 원본이 노션이라 여기서 고치면 다음 동기화가 덮어쓴다는
    이유였고, 그건 지금도 사실입니다. 다만 zip 을 만들기 귀찮은 경우가 더 잦아서, 고치는
    것을 막는 대신 **고친 사실을 화면이 말하도록** 바꿨습니다(``edited_at``).
    """
    from ...agents.policy_sync import knowledge_slug
    from ...db.models import PolicySource
    from ...db.session import SessionLocal
    from .policy_docs import MODES

    with SessionLocal() as session:
        rows = (
            session.query(PolicySource)
            .order_by(PolicySource.mode, PolicySource.id)
            .all()
        )
        return {
            "modes": [{"key": key, "label": label} for key, label in MODES],
            "rows": [
                {
                    "id": row.id,
                    "label": row.label,
                    "title": row.title,
                    "mode": row.mode,
                    # 라우터가 이 문서를 고를 때 부르는 이름. 「항상 적용」 문서에는 없습니다
                    # — 그건 고르는 대상이 아니라 모든 프롬프트에 통째로 들어갑니다.
                    "slug": knowledge_slug(row) if row.mode == "knowledge" else "",
                    "body": row.body,
                    "chars": len(row.body or ""),
                        # 라우터가 이 문서를 고를 때 읽는 한 줄. 비면 본문 앞부분이 대신합니다.
                    "usage_note": row.usage_note or "",
                    "updated_at": row.updated_at,
                    "version": row.version or 1,
                }
                for row in rows
            ],
        }


@router.get("/api/ui/documents/{kind}/{document_id}/revisions")
def ui_document_revisions(kind: str, document_id: int):
    """그 문서의 이전 판본들, 최신 먼저.

    **이메일 템플릿과 정책 문서가 같은 라우트를 씁니다.** 보고 싶은 것이 같기 때문입니다 —
    언제, 누가, 무엇을, 그때 본문은 무엇이었나. 종류마다 라우트를 두면 화면도 둘이 되고,
    둘 중 하나에만 이력이 달리는 날이 옵니다(실제로 그랬습니다 — 0096).

    ``kind`` 는 경로에서 온 문자열이라 **허용 목록으로 거릅니다.** 그대로 조회에 넣으면
    아무 문자열이나 지나가고, 그때 돌아오는 빈 목록은 「이력이 없다」와 구별되지 않습니다.
    """
    from ...db.revisions import KIND_LABELS, KINDS, history
    from ...db.session import SessionLocal

    if kind not in KINDS:
        raise HTTPException(status_code=400, detail="알 수 없는 문서 종류입니다")
    with SessionLocal() as session:
        return {
            "kind": kind,
            "kind_label": KIND_LABELS[kind],
            "document_id": document_id,
            "revisions": history(session, kind, document_id),
        }


@router.get("/api/ui/pipeline/{stage}/cards")
def ui_pipeline_page(stage: str, offset: int = 0):
    """One more page of a column — the same paging the Jinja board scrolls into."""
    from .customer_ops import BOARD_CARDS_PER_STAGE, VALID_PIPELINE_STAGES, _pipeline_rows

    if stage not in VALID_PIPELINE_STAGES:
        raise HTTPException(status_code=404, detail="지원하지 않는 파이프라인 단계입니다")
    offset = max(offset, 0)
    rows, totals = _pipeline_rows(stage=stage, limit=BOARD_CARDS_PER_STAGE, offset=offset)
    return {
        "cards": [_card(row) for row in rows],
        "next_offset": offset + len(rows),
        "has_more": offset + len(rows) < totals.get(stage, 0),
    }


# --------------------------------------------------------------------------- #
# 수주 고객
#
# 목록과 상세를 **한 번에** 보냅니다. 상세는 8개 섹션이 한 화면에 다 있고 각각이 별도
# 요청이면 열 때마다 왕복이 여덟 번인데, 서울에서 이 서비스까지 왕복이 200~370ms 입니다.
# 계약 하나가 1KB 남짓이라 통째로 실어 보내는 편이 싸고, 여는 순간 그려집니다.
#
# 파생값(다음 지급일·수금율·월간 매출·고객 종류…)은 저장하지 않고 여기서 계산합니다.
# 저장해 두면 원본이 바뀌었는데 파생값은 안 바뀐 행이 생기고, 그건 화면에 안 보입니다.
# --------------------------------------------------------------------------- #
def _won_contract(contract, today) -> dict:
    from ...common import won

    grants = contract.credit_grants
    payments = contract.payments
    next_grant = won.next_credit_grant(contract)
    next_pay = won.next_payment(contract)
    return {
        "id": contract.id,
        "seq": contract.seq,
        "label": f"{contract.seq}차 계약",
        "state": won.contract_state(contract, today),
        "ticket_id": contract.ticket_id,
        "deal_type": contract.deal_type,
        "starts_on": contract.starts_on,
        "ends_on": contract.ends_on,
        "months": won.months_between(contract.starts_on, contract.ends_on),
        "doc_types": contract.doc_types or [],
        "credits": contract.credits,
        "currency": contract.currency,
        # 금액은 통화가 정한 한 칸만 저장되고 나머지는 계산입니다: 원화는 공급가를 받아
        # 총액을 +10% 로, 그 외는 총액을 받고 공급가 칸이 없습니다. 분당 단가도 계산값
        # 입니다 — 금액 ÷ (크레딧 ÷ 60).
        "amount_incl_vat": won.total_amount(contract),
        # 총액으로 적힌 계약도 채웁니다(총액 ÷ 1.1). 워크북의 공급가 열이 회계가 합계를
        # 내는 칸이라 비면 그 행만 빠지고, 화면과 시트가 같은 값이어야 합니다.
        "amount_excl_vat": won.supply_amount(contract),
        # 분당 단가의 기준이 VAT 포함 금액인가 — 화면의 「공급가 선택」이 고른 값입니다.
        "vat_included": won.vat_included(contract),
        # 부가세가 붙는 계약인가. **통화가 아니라 고객이 정합니다**(이관 0075). 폼이 금액
        # 칸을 한 개 그릴지 두 개 그릴지가 여기서 갈립니다.
        "vat_applicable": won.vat_applicable(contract),
        # 그 계약에 적용할 환율과 기준 날짜. 비어 있으면 저장할 때 계약일 고시가로 채웁니다.
        "fx_rate": contract.fx_rate,
        "fx_on": contract.fx_on,
        # 중도 해지일과 크레딧 사용량. 사용량은 수동 입력이라 비어 있는 것이 정상입니다.
        "terminated_on": contract.terminated_on,
        "credits_used": contract.credits_used,
        "unit_price": won.unit_price(contract),
        "payment_method": contract.payment_method,
        "payment_type": contract.payment_type,
        "installments": contract.installments,
        "first_payment_on": contract.first_payment_on,
        "billing_email": contract.billing_email,
        # 고객사 측 담당자. 계약마다 다를 수 있어 고객이 아니라 여기 있습니다(0103).
        "contact_name": contract.contact_name,
        "contact_info": contract.contact_info,
        "note": contract.note,
        "revenue_from": won.revenue_start_month(contract),
        "revenue_from_set": bool(contract.revenue_from),
        "monthly_revenue": won.monthly_revenue(contract),
        "plan": contract.plan,
        "plan_name": contract.plan_name,
        "perso_email": contract.perso_email,
        "plan_starts_on": contract.plan_starts_on,
        "plan_ends_on": contract.plan_ends_on,
        "plan_days_left": (
            (won.parse_date(contract.plan_ends_on) - today).days
            if won.parse_date(contract.plan_ends_on)
            else None
        ),
        "invite_limit": contract.invite_limit,
        "queue_limit": contract.queue_limit,
        "concurrent_jobs": contract.concurrent_jobs,
        "space_count": contract.space_count,
        "space_seq": contract.space_seq,
        "granted_credits": won.granted_credits(contract),
        "collected": won.collected(contract),
        "next_credit_on": next_grant.grant_on if next_grant else None,
        "next_credit_amount": next_grant.amount if next_grant else None,
        # 몇 번째 회차인지. 액션 보드가 "1,800 크레딧" 만 보여 주면 그게 마지막 회차인지
        # 열두 번 중 두 번째인지 알 수 없어서, 열어 봐야 판단이 됩니다.
        "next_credit_no": next_grant.no if next_grant else None,
        "next_credit_total": next_grant.total if next_grant else None,
        "next_pay_on": next_pay.paid_on if next_pay else None,
        "next_pay_amount": next_pay.amount if next_pay else None,
        "next_pay_no": next_pay.no if next_pay else None,
        "next_pay_total": next_pay.total if next_pay else None,
        "credit_grants": [
            {
                "id": g.id, "no": g.no, "total": g.total, "grant_on": g.grant_on,
                "amount": g.amount, "granted_by": g.granted_by, "done": g.done, "memo": g.memo,
            }
            for g in grants
        ],
        "payments": [
            {
                "id": p.id, "no": p.no, "total": p.total, "paid_on": p.paid_on,
                "amount": p.amount, "done": p.done,
                "fx_rate": p.fx_rate, "fx_on": p.fx_on,
            }
            for p in payments
        ],
    }


def _won_client(client, today, *, full: bool, contact=None) -> dict:
    from decimal import Decimal

    from ...common import won

    active = won.active_contract(client, today)
    upcoming = won.upcoming_contracts(client, today)
    # 이번 달에 이 고객이 얹은 금액 — 목록의 「이번달 매출」 칸이자, 위 카드가 더하는 값.
    # **계약 전부를 훑습니다.** 행에 실리는 계약은 활성 하나뿐이라, 그것만 보면 한 고객의
    # 다른 계약이 돌고 있어도 안 잡힙니다. 통화는 안 섞습니다: 환산은 카드가 오늘 고시가로
    # 한 번만 하고, 행마다 다시 하면 같은 화면에 서로 다른 환율이 생깁니다.
    month = today.strftime("%Y-%m")
    month_revenue: dict[str, Decimal] = {}
    for contract in client.contracts:
        amount = won.revenue_in_month(contract, month)
        if amount:
            code = (contract.currency or "KRW").upper()
            month_revenue[code] = month_revenue.get(code, Decimal(0)) + amount
    payload = {
        "client_id": client.client_id,
        "company": client.company,
        "customer_type": won.client_type(client.client_id),
        "industry": client.industry,
        "country": client.country,
        # **적어 둔 값이 없으면 번호대에서 되짚습니다**(won.department) — 요약 카드와
        # 예상 MRR 이 GTM 만 더할 때 쓰는 것과 같은 함수입니다. 여기서 원본 열을 그대로
        # 내려보내던 시절에는, 부서 칸이 빈 고객이 카드에는 잡히고 담당부서 필터에는
        # 안 걸렸습니다 — 같은 화면의 두 숫자가 서로 다른 정의를 쓴 것입니다.
        "department": won.department(client),
        # 연결된 인바운드 연락처의 이메일·전화. 목록 검색이 씁니다 — 클레임이나 결제
        # 문의는 회사 이름이 아니라 메일 주소로 기억되는 일이 흔합니다. 아웃바운드·
        # Interactive·AX 고객은 연락처가 없어 비는 것이 정상입니다.
        "email": getattr(contact, "email", None),
        "phone": getattr(contact, "phone", None),
        "first_won_on": client.first_won_on,
        # 계약 기간에서 나옵니다 — 저장하지 않습니다(won.plan_status).
        "plan_status": won.plan_status(client, today),
        "owner": client.owner,
        "contact_id": client.contact_id,
        "setup_count": len(upcoming),
        "active": _won_contract(active, today) if active else None,
        "month_revenue": month_revenue,
    }
    if full:
        payload["contracts"] = [_won_contract(c, today) for c in client.contracts]
    else:
        # 목록은 진행 중 계약 하나만 씁니다. 전체 차수는 상세에서.
        payload["contract_count"] = len(client.contracts)
    return payload


def _recent_months(today: date, count: int) -> list[str]:
    """이번 달로 끝나는 최근 ``count`` 개월, 오래된 것부터. ``YYYY-MM``."""
    year, month = today.year, today.month
    out: list[str] = []
    for back in range(count - 1, -1, -1):
        total = year * 12 + (month - 1) - back
        out.append(f"{total // 12}-{total % 12 + 1:02d}")
    return out


def _both_currencies(amount: Decimal, code: str, rate: Decimal) -> dict[str, Decimal]:
    """한 금액을 KRW·USD 두 통화로. 환율이 0 이면 환산하지 않습니다(0 나눗셈)."""
    if code == "KRW":
        return {"KRW": amount, "USD": amount / rate if rate else Decimal(0)}
    return {"KRW": amount * rate, "USD": amount}


def _contract_rate(contract, fallback: Decimal) -> Decimal:
    """그 계약에 적용할 환율. **계약에 박힌 값이 먼저입니다** — 오늘 고시가로 과거를 다시
    환산하면 마감한 달의 숫자가 오늘 환율에 따라 움직입니다."""
    from ...common import won

    return won._decimal(getattr(contract, "fx_rate", None)) or fallback


def _mrr_cells(contract, months: list[str], fallback: Decimal) -> dict[str, dict[str, Decimal]]:
    from ...common import won

    code = (contract.currency or "KRW").upper()
    rate = _contract_rate(contract, fallback)
    cells: dict[str, dict[str, Decimal]] = {}
    for month in months:
        amount = won.revenue_in_month(contract, month)
        if amount:
            cells[month] = _both_currencies(amount, code, rate)
    return cells


def _cash_cells(contract, months: list[str], fallback: Decimal) -> dict[str, dict[str, Decimal]]:
    """월 매출 — **결제 회차가 잡힌 달**에 그 회차 금액을 통째로. 현금흐름 관점입니다.

    일시불이면 한 달에 전액, 할부면 회차마다 그 달에. MRR 처럼 기간에 나누지 않습니다 —
    같은 계약이 두 지표에서 다르게 보이는 것이 이 화면의 요점입니다.

    환율은 **그 회차에 박힌 값**이 먼저입니다(입금한 날의 고시가). 없으면 계약 환율, 그것도
    없으면 오늘 고시가입니다.

    날짜가 있는 회차는 아직 입금 전이어도 셉니다: 이 표는 「받은 돈」이 아니라 「그 달에
    잡히는 매출」이고, 수금 여부는 상세 화면의 수금율이 따로 말합니다.
    """
    from ...common import won

    code = (contract.currency or "KRW").upper()
    wanted = set(months)
    cells: dict[str, dict[str, Decimal]] = {}
    for payment in getattr(contract, "payments", None) or ():
        month = str(payment.paid_on or "")[:7]
        amount = won._decimal(payment.amount)
        if month not in wanted or not amount:
            continue
        rate = won._decimal(payment.fx_rate) or _contract_rate(contract, fallback)
        cell = cells.setdefault(month, {"KRW": Decimal(0), "USD": Decimal(0)})
        for currency, value in _both_currencies(amount, code, rate).items():
            cell[currency] += value
    return cells


def _add_series(target: dict, buckets, months: list[str], cells: dict) -> None:
    """계약 하나의 달별 금액을 담당부서 묶음에 더합니다. 「전체」도 여기서 같이 만듭니다 —
    화면이 부서별 값을 다시 더하면 그 덧셈이 두 곳에 생깁니다."""
    if not cells:
        return
    for bucket in buckets:
        by_month = target.setdefault(bucket, {})
        for month, amounts in cells.items():
            cell = by_month.setdefault(month, {"KRW": Decimal(0), "USD": Decimal(0)})
            for currency, value in amounts.items():
                cell[currency] += value


def _only(cells: dict, month: str | None) -> dict:
    """그 한 달의 칸만. New 계열은 「고객이 된 달」의 값만 세므로 나머지를 여기서 버립니다."""
    cell = cells.get(month) if month else None
    return {month: cell} if cell else {}


def _series_floats(series: dict) -> dict:
    return {
        bucket: {
            month: {code: float(value) for code, value in amounts.items()}
            for month, amounts in by_month.items()
        }
        for bucket, by_month in series.items()
    }


@router.get("/api/ui/won-customers")
def ui_won_customers():
    """수주 고객 목록 + 요약 카드 + 액션 보드 + 수주 전환 대기 — 한 화면이라 한 번에."""
    from datetime import date

    from decimal import Decimal

    from ...common import won
    from ...common.config import settings as app_settings
    from ...db.models import Client, ClientContract, PendingWon
    from ...db.session import SessionLocal
    from ...integrations.fx import last_error as fx_error
    from ...integrations.fx import usd_krw_today
    from sqlalchemy.orm import selectinload

    today = date.today()
    try:
        fx = usd_krw_today()
    except Exception:  # 환율을 못 가져와도 목록은 떠야 합니다
        fx = None
    month = today.strftime("%Y-%m")
    # 이번 달로 끝나는 **6개월** (2026-08-19, 운영자 지시 — 1년치는 길다). MRR 은 한 달만
    # 보면 늘었는지 줄었는지를 알 수 없어서, 카드가 옆으로 넓어진 자리에 이 구간을 펼칩니다.
    # 구간을 정하는 곳은 여기 하나입니다 — 화면은 `data.months` 를 그대로 그리고, 눈금
    # 단위도 그 구간의 최댓값에서 나옵니다.
    months = _recent_months(today, 6)
    # 담당부서 → 달 → 통화 → 금액. **두 통화 다 채웁니다** — 어느 쪽으로 볼지는 화면이
    # 고르고, 환산은 여기서 계약마다 그 계약의 환율로 한 번만 합니다.
    mrr_months: dict[str, dict[str, dict[str, Decimal]]] = {}
    cash_months: dict[str, dict[str, dict[str, Decimal]]] = {}
    # 같은 모양의 「New」 두 벌. **그 달에 고객이 된 고객만** 담습니다 — 각 달의 총액 중
    # 신규가 얼마인지가 이 화면에서 가장 자주 묻는 질문이고, 화면이 행을 걸러 세면 그
    # 필터가 곧 정의가 됩니다(그리고 정의가 둘이 됩니다).
    mrr_new_months: dict[str, dict[str, dict[str, Decimal]]] = {}
    cash_new_months: dict[str, dict[str, dict[str, Decimal]]] = {}
    with SessionLocal() as session:
        clients = (
            session.query(Client)
            .options(
                # 계약만 미리 읽고 그 **아래**는 안 읽었습니다. 그런데 활성 계약 하나는
                # 크레딧 회차와 결제 회차를 전부 만집니다 — 그래서 고객 40 · 계약 80 짜리
                # 장부 하나를 그리는 데 쿼리가 163 번 나갔습니다(실측). 왕복 하나가 200ms
                # 인 환경에서 그건 30초입니다. 아래 상세 라우트가 이미 쓰고 있던 것과 같은
                # 옵션이고, 같은 것을 두 번 적기보다 계약이 늘수록 나빠지는 쪽을 막는 게
                # 먼저입니다: 163 → 6, 결과는 한 글자도 다르지 않습니다.
                selectinload(Client.contracts).selectinload(ClientContract.credit_grants),
                selectinload(Client.contracts).selectinload(ClientContract.payments),
            )
            .order_by(Client.company)
            .all()
        )
        # 연락처는 한 번에 읽습니다 — 고객마다 한 번씩 읽으면 목록 하나에 쿼리가 고객 수만큼
        # 늘어납니다(위 selectinload 를 단 것과 같은 이유).
        contact_ids = {client.contact_id for client in clients if client.contact_id}
        contacts = (
            {
                contact.id: contact
                for contact in session.query(Contact).filter(Contact.id.in_(contact_ids))
            }
            if contact_ids
            else {}
        )
        rows = [
            _won_client(client, today, full=False, contact=contacts.get(client.contact_id))
            for client in clients
        ]
        # 카드는 **행이 이미 들고 있는 값**을 더합니다. 따로 세면 목록의 「이번달 매출」 칸과
        # 카드가 언젠가 어긋나고, 어긋난 뒤에는 어느 쪽이 맞는지 아무도 모릅니다.
        #
        # **부서별로 나눠서** 더합니다. Interactive 와 AX 는 각자 매출을 따로 보므로, 셋을
        # 한 숫자로 더한 카드는 아무 팀의 숫자도 아닙니다 — 화면 위의 담당부서 고르개가 어느
        # 묶음을 볼지 정하고, 그 고르개가 목록도 같이 거릅니다. 「전체」도 여기서 같이 만듭니다:
        # 화면이 부서별 값을 다시 더하면 그 덧셈이 두 곳에 생깁니다.
        today_rate = fx[0] if fx else Decimal(str(app_settings.MRR_FX_RATE))
        # **오늘 고시가로 떨어진 계약이 몇 건인가.** 환율은 계약마다 박혀 있어야 하고
        # (`_fill_contract_fx`, 이관 0102), 비어 있는 계약만 이 값으로 환산됩니다 — 그런
        # 계약의 USD 숫자는 **매일 달라집니다.** 0건이면 화면은 아무 말도 안 하고, 있으면
        # 그 수를 적습니다. 조용히 떨어지는 것이 문제이지 떨어지는 것 자체가 아닙니다.
        without_rate = 0
        for client in clients:
            buckets = (won.department(client) or "미지정", won.ALL_DEPARTMENTS)
            # 그 고객이 처음 잡히는 달. 그 달의 칸만 New 로도 셉니다 — 다음 달부터 그
            # 고객은 신규가 아니고, 그때는 총액에만 남습니다.
            #
            # **계열마다 자가 다릅니다.** MRR 은 인식하는 달로, 매출은 입금하는 달로 칸을
            # 만들고 그 둘은 흔히 다른 달입니다(계약은 먼저 맺고 사용은 늦게 시작합니다).
            # 한 자로 재면 그 차이만큼 신규 고객이 **어느 달의 New 에도** 안 잡히는데,
            # 화면에는 큰 막대 옆에 「New ₩0」 이 설 뿐 틀렸다는 표시가 없습니다.
            new_revenue = won.first_revenue_month(client)
            new_cash = won.first_cash_month(client)
            for contract in client.contracts:
                if won._decimal(getattr(contract, "fx_rate", None)) is None:
                    without_rate += 1
                mrr_cells = _mrr_cells(contract, months, today_rate)
                cash_cells = _cash_cells(contract, months, today_rate)
                _add_series(mrr_months, buckets, months, mrr_cells)
                _add_series(cash_months, buckets, months, cash_cells)
                _add_series(mrr_new_months, buckets, months, _only(mrr_cells, new_revenue))
                _add_series(cash_new_months, buckets, months, _only(cash_cells, new_cash))
        pending = (
            session.query(PendingWon)
            .filter(PendingWon.status == "pending")
            .order_by(PendingWon.created_at.desc())
            .all()
        )
        # Renewal 인지 Contract 인지는 **우리 장부가 압니다** — 그 Client ID 아래 계약이
        # 이미 있으면 재계약이고, 없으면 첫 계약입니다. HubSpot 의 Won type 을 읽지 않는
        # 이유가 이것입니다: 그 값은 담당자가 파이프라인에서 고른 것이라 틀릴 수 있고,
        # 계약 수는 틀릴 수가 없습니다.
        known = {client.client_id: client for client in clients}
        waiting = []
        for item in pending:
            client = known.get(item.client_id) if item.client_id else None
            seq = len(client.contracts) + 1 if client else 1
            waiting.append({
                "id": item.id, "ticket_id": item.ticket_id,
                # 이미 등록된 고객이면 **장부의 이름**이 맞습니다. 티켓의 회사명은 문의
                # 시점 값이라 그 뒤로 바뀌었을 수 있습니다.
                "company": (client.company if client else None) or item.company,
                "client_id": item.client_id,
                "won_type": "Renewal" if seq > 1 else "Contract",
                "next_seq": seq,
                "known": client is not None,
                "won_on": item.won_on,
            })

    # 액션 보드 둘. 목록을 한 번 더 도는 대신 위에서 만든 payload 를 그대로 씁니다.
    credit_due, pay_due = [], []
    for row in rows:
        active = row["active"]
        if row["plan_status"] == "사용 중단" or not active:
            continue
        if active["next_credit_on"]:
            credit_due.append({
                "client_id": row["client_id"], "company": row["company"],
                "on": active["next_credit_on"], "amount": active["next_credit_amount"],
                "no": active["next_credit_no"], "total": active["next_credit_total"],
            })
        if active["next_pay_on"]:
            pay_due.append({
                "client_id": row["client_id"], "company": row["company"],
                "on": active["next_pay_on"], "amount": active["next_pay_amount"],
                "no": active["next_pay_no"], "total": active["next_pay_total"],
                "currency": active["currency"],
            })
    credit_due.sort(key=lambda x: x["on"] or "9999")
    pay_due.sort(key=lambda x: x["on"] or "9999")

    return {
        "today": today.isoformat(),
        "rows": rows,
        "pending": waiting,
        "boards": {"credit": credit_due, "payment": pay_due},
        # **환율은 계약마다 박혀 있습니다** — 이 카드가 쓰는 「적용 환율」이라는 것은
        # 없습니다. 한동안 오늘 고시가를 한 줄로 적어 뒀는데, 계약마다 환산하도록 바뀐
        # 뒤로 그 줄은 **아무 숫자도 설명하지 않았습니다**(2026-08-31 운영자 지적).
        #
        # 남는 것은 하나: 환율이 비어 있어 오늘 고시가로 떨어진 계약이 몇 건인가. 그런
        # 계약의 USD 숫자는 매일 달라지고, 그건 화면에 보여야 합니다. 0건이면 화면은
        # 아무 말도 안 합니다.
        "contracts_without_rate": without_rate,
        # 그 계약들이 어느 날 값으로 환산됐는지. 없으면 설정값으로 떨어진 것이고, 왜
        # 못 가져왔는지가 없으면 「설정값」이 막다른 길이 됩니다.
        "fallback_fx_on": fx[1] if fx else None,
        "fx_error": None if fx else fx_error(),
        # 「월별 MRR」과 「월 매출」, 담당부서별 · 달별 · 두 통화.
        #
        # **환산은 계약마다 그 계약의 환율로 합니다**(`client_contracts.fx_rate`). 오늘
        # 고시가로 전부 환산하던 시절에는 같은 계약의 지난달 매출이 이번 달에 달라 보였고,
        # 마감한 달의 숫자가 오늘 환율에 따라 움직였습니다. 계약에 환율이 없는 옛 행만
        # 오늘 고시가로 떨어집니다.
        #
        # **두 지표는 다른 것을 셉니다.** `mrr` 은 플랜 기간에 균등 배분한 인식 매출이고,
        # `cash` 는 결제 회차가 잡힌 달에 통째로 얹는 현금흐름입니다. 한 화면에 같이 두는
        # 이유는 둘이 갈릴 때가 그 계약을 봐야 할 때이기 때문입니다.
        #
        # 화면이 행을 걸러 더하지 않게 여기서 다 계산합니다: 화면의 필터가 곧 정의가 되면
        # 언젠가 정의가 둘이 됩니다(실제로 플랜 상태로 거르고 있었고, 그때 세팅중·사용
        # 중단 고객이 통째로 빠졌습니다). 고객의 **모든** 계약을 봐야 하는데 행에는 활성
        # 계약 하나만 실려 있는 것도 같은 이유입니다.
        "month": month,
        "months": months,
        "mrr_months": _series_floats(mrr_months),
        "cash_months": _series_floats(cash_months),
        # **그 달에 고객이 된 고객만** (2026-09-02 운영자 지시). 같은 모양이라 화면이
        # 같은 자로 재고, 총액 막대 위에 얹힙니다 — 그래서 언제나 총액의 부분집합이어야
        # 합니다. 「고객이 된 달」을 `won.acquired_month` 가 인식 시작월로 재는 이유가
        # 그것입니다: 다른 자로 재면 New 가 총액보다 커지는 달이 생깁니다.
        "mrr_new_months": _series_floats(mrr_new_months),
        "cash_new_months": _series_floats(cash_new_months),
        "options": {
            "industries": list(won.INDUSTRIES),
            "plans": list(won.PLANS),
            # 「내림」이 뒤에 붙습니다 — 내린 고객은 목록에서 숨기므로, 이 고르개가
            # 그들을 다시 보는 유일한 길입니다.
            "plan_statuses": [*won.PLAN_STATUSES, won.RETIRED_PLAN_STATUS],
            "deal_types": list(won.DEAL_TYPES),
            "doc_types": list(won.DOC_TYPES),
            "payment_methods": list(won.PAYMENT_METHODS),
            "payment_types": list(won.PAYMENT_TYPES),
            "currencies": list(won.CURRENCIES),
            "customer_types": list(won.ALLOCATABLE_BANDS),
            "departments": list(won.DEPARTMENTS),
            # 「전체」는 부서가 아니라 그 셋을 합친 묶음입니다 — 화면이 그 이름을 지어내면
            # 서버가 보낸 키와 어긋납니다.
            "all_departments": won.ALL_DEPARTMENTS,
        },
    }


@router.get("/api/ui/won-customers/{client_id}")
def ui_won_customer(client_id: int):
    """고객 하나 — 계약 전체와 그 아래 회차까지. 소통 히스토리는 고객 단위입니다."""
    from datetime import date

    from sqlalchemy.orm import selectinload

    from ...db.models import Client, ClientContract, CustomerInteraction
    from ...db.session import SessionLocal

    today = date.today()
    with SessionLocal() as session:
        client = (
            session.query(Client)
            .options(
                selectinload(Client.contracts).selectinload(ClientContract.credit_grants),
                selectinload(Client.contracts).selectinload(ClientContract.payments),
            )
            .filter(Client.client_id == client_id)
            .one_or_none()
        )
        if client is None:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        payload = _won_client(
            client,
            today,
            full=True,
            contact=session.get(Contact, client.contact_id) if client.contact_id else None,
        )
        # 협상 단계 대화까지 한 타임라인에 쌓입니다 — 계약이 생기기 전 기록이 여기 있습니다.
        comms = []
        if client.contact_id:
            comms = (
                session.query(CustomerInteraction)
                .filter(CustomerInteraction.contact_id == client.contact_id)
                .order_by(CustomerInteraction.happened_at.desc())
                .limit(200)
                .all()
            )
        payload["comms"] = [
            {
                "id": item.id, "channel": item.channel, "handler": item.handler,
                "subject": item.subject, "summary": item.summary,
                "happened_at": item.happened_at, "contract_seq": item.contract_seq,
            }
            for item in comms
        ]
    return payload
