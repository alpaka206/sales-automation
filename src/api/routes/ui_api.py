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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...db.models import Contact, Conversation, CustomerProfile

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
    conversation: Conversation = row["conversation"]
    contact: Contact = row["contact"]
    profile: CustomerProfile | None = row["profile"]
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
        "link_message_id": row["link_message_id"],
        "last_activity": row["last_activity"],
        "stage": row["stage"],
        "temperature": profile.lead_temperature if profile else None,
    }


@router.get("/api/ui/dashboard")
def ui_dashboard(_request: Request):
    from .dashboard import _dashboard_context

    context = _dashboard_context()
    return {
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


@router.get("/api/ui/overview")
def ui_overview():
    """전체 대시보드. A roll-up of screens that each own their own numbers."""
    from .dashboard import _overview_context

    return _overview_context()


@router.get("/api/ui/contracts")
def ui_contracts(status: str = "", q: str = ""):
    """수주 고객. The contract book, plus the summary the overview shows — same two
    builders, so the money on one screen cannot disagree with the money on the other."""
    from .customer_ops import CONTRACT_STATUS_LABELS, _contract_rows, _contract_summary

    return {
        "rows": _contract_rows(status=status, query=q),
        "summary": _contract_summary(),
        "status_options": [{"key": key, "label": label} for key, label in CONTRACT_STATUS_LABELS],
        "filter_status": status,
        "query": q,
    }


@router.get("/api/ui/messages")
def ui_messages(status: str = "awaiting", stage: str = "", sort: str = "oldest"):
    """회신 및 검토. Returned as built — the context is already plain dicts, and every
    filter value is allow-listed inside the builder, so nothing is validated twice."""
    from .messages import _messages_list_context

    return _messages_list_context(status=status, stage=stage, sort=sort)


@router.get("/api/ui/messages/{message_id}")
async def ui_message_detail(message_id: int):
    """티켓 세부 내역. The builder already returns plain dicts — including the Korean
    translations it fills in concurrently — so this adds nothing but the stage labels the
    route adds for the template."""
    from .customer_ops import PIPELINE_STAGES
    from .messages import _message_detail_context, _translate_inbound_bubbles

    # In a thread, not on the event loop. One open costs ~11 sequential round trips to
    # Postgres, and on the loop every other request — the SSE stream, the 15-second queue
    # poll, another operator's screen — waits behind them. That is what "살짝 늦게 뜬다"
    # was. It does not make this call faster; it stops it from slowing everything else.
    context = await asyncio.to_thread(_message_detail_context, message_id)
    if not context:
        raise HTTPException(status_code=404, detail="메시지를 찾을 수 없습니다")
    await _translate_inbound_bubbles(context)
    context["stage_labels"] = {key: label for key, label, _ in PIPELINE_STAGES}
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
            }
            for row in rows
        ],
        "stage_options": [{"key": key, "label": label} for key, label, _ in PIPELINE_STAGES],
        "filter_stage": stage,
        "query": q,
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
def ui_operations(period: str = "month"):
    """인사이트. The follow-up ladder and the renewal window, from the same builder the
    page renders — these numbers must not have a second definition."""
    from .customer_ops import _operations_context

    context = _operations_context(period)
    return {
        "period": context["period"],
        "chart": context["chart"],
        "line_points": context["line_points"],
        "country_rows": context["country_rows"],
        "inbound_total": context["inbound_total"],
        "inbound_in_period": context["inbound_in_period"],
        "qualified_count": context["qualified_count"],
        "average_score": context["average_score"],
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
            "error": message.post_send_sync_error,
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


@router.get("/api/ui/customers/{contact_id}")
def ui_customer_detail(contact_id: int):
    """고객 상세. The builder returns ORM rows; the screen needs their fields."""
    from .customer_ops import _customer_context

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
                "customer_state", "pipeline_stage", "lead_temperature", "qualification",
                "industry", "user_seq", "current_plan", "source", "next_action",
                "next_action_at", "lost_reason", "notes", "last_synced_at",
            )
        }
        if profile
        else None,
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
# everything else is a body the send path fetches by exact name (auto_ack, the reply
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


def _base_key(key: str, all_keys: set[str]) -> str:
    """The row this one is a language of, or itself.

    ``auto_ack_en`` belongs under ``auto_ack``; the screen lists one entry and the
    language is chosen inside it. Two rows in the list for one thing reads as two things.

    Signatures never group. They have no language at all (0063) — the operator picks one
    on the draft — so two of them are two signatures, whatever their keys end in. A
    signature named "x" and one named "x en" would otherwise land under one entry with a
    language switcher offering 전체 twice.

    Otherwise stripped ONLY when the shorter key actually exists, so an unrelated row
    ending in ``_en`` keeps its own entry.
    """
    import re

    from ...db.email_templates import SIGNATURE_KEY_PREFIX

    if key.startswith(SIGNATURE_KEY_PREFIX):
        return key
    base = re.sub(r"_(ko|en|all)$", "", key)
    return base if base != key and base in all_keys else key


@router.get("/api/ui/email-templates")
def ui_email_templates():
    """Grouped, not one flat list — and each group says what it is for.

    Bodies ride along. There are a handful of rows and the largest is a 1.1 KB signature,
    while a second request costs a full round trip — measured at 200-370 ms from Seoul to
    this service, and that is the FLOOR: /healthz, which touches Postgres, takes the same
    as a static file, so the distance is the cost, not the query. Sending ~10 KB once
    means opening a template is instant instead of "a form appears, then changes".
    """
    from ...db.models import EmailTemplate
    from ...db.session import SessionLocal

    from ...db.models import PolicySource

    with SessionLocal() as session:
        policy_count = session.query(PolicySource).count()
        rows = session.query(EmailTemplate).order_by(EmailTemplate.updated_at.desc()).all()
        all_keys = {row.key for row in rows}
        items = [
            {
                "id": row.id,
                # The key, because two of these rows hold nothing but a URL and the screen
                # has to know that to stop asking for a language and an HTML preview.
                "key": row.key,
                "name": row.name,
                "language": row.language or "all",
                "updated_at": row.updated_at,
                "kind": _template_kind(row.key),
                # Which list entry this row sits under. Rows sharing one are the same
                # template in different languages.
                "base_key": _base_key(row.key, all_keys),
                "body": row.body or "",
                "subject": row.subject or "",
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
                "count": policy_count if key == "policy"
                else sum(1 for item in items if item["kind"] == key),
                # New rows are only reachable as signatures: the send path resolves the
                # other kind by exact name, so a template created here would be a row
                # nothing can ever read. Editing the existing ones is the point.
                "can_create": key == "signature",
                "read_only": key == "policy",
            }
            for key, label in TEMPLATE_KINDS
        ],
        "items": items,
    }


@router.get("/api/ui/quote-policy")
def quote_policy() -> dict:
    """The tier table the 견적 계산기 screen prices against.

    Behind the console's auth gate rather than on /static because the policy is internal
    sales data — `tier_to_client_dict` already strips contribution margin, and the gate
    keeps the rest of it off the public mount.
    """
    from ...common.quote_tiers import policy_client

    return policy_client()


@router.get("/api/ui/policy-docs")
def ui_policy_docs():
    """정책 문서 — 등록부 + 사본 + 그 문서를 어떤 문의에 쓸지.

    한동안 읽기 전용이었습니다: 원본이 노션이라 여기서 고치면 다음 동기화가 덮어쓴다는
    이유였고, 그건 지금도 사실입니다. 다만 zip 을 만들기 귀찮은 경우가 더 잦아서, 고치는
    것을 막는 대신 **고친 사실을 화면이 말하도록** 바꿨습니다(``edited_at``).
    """
    from ...db.models import PolicySource
    from ...db.session import SessionLocal
    from .policy_docs import MODES

    with SessionLocal() as session:
        rows = (
            session.query(PolicySource)
            .order_by(PolicySource.mode, PolicySource.order_index, PolicySource.id)
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
                    "body": row.body,
                    "chars": len(row.body or ""),
                    "subject": row.subject or "",
                    # 라우터가 이 문서를 고를 때 읽는 한 줄. 비면 본문 앞부분이 대신합니다.
                    "usage_note": row.usage_note or "",
                    "effective_on": row.effective_on,
                    "edited_at": row.edited_at,
                }
                for row in rows
            ],
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
        "amount_incl_vat": contract.amount_incl_vat,
        "amount_excl_vat": contract.amount_excl_vat,
        "unit_price": contract.unit_price,
        "unit_currency": contract.unit_currency,
        "unit_fx_rate": contract.unit_fx_rate,
        "payment_method": contract.payment_method,
        "payment_type": contract.payment_type,
        "installments": contract.installments,
        "first_payment_on": contract.first_payment_on,
        "billing_email": contract.billing_email,
        "note": contract.note,
        "renewal_plan": contract.renewal_plan,
        "stop_reason": contract.stop_reason,
        "memo": contract.memo,
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
        "claims": [
            {
                "id": c.id, "kind": c.kind, "happened_on": c.happened_on,
                "compensation": c.compensation, "progress": c.progress, "action_on": c.action_on,
            }
            for c in contract.claims
        ],
    }


def _won_client(client, today, *, full: bool) -> dict:
    from ...common import won

    active = won.active_contract(client, today)
    upcoming = won.upcoming_contracts(client, today)
    payload = {
        "client_id": client.client_id,
        "company": client.company,
        "customer_type": won.client_type(client.client_id),
        "industry": client.industry,
        "country": client.country,
        "department": client.department,
        "contact_name": client.contact_name,
        "contact_info": client.contact_info,
        "first_won_on": client.first_won_on,
        "plan_status": client.plan_status,
        "owner": client.owner,
        "contact_id": client.contact_id,
        "setup_count": len(upcoming),
        "open_claims": len(won.open_claims(client)),
        "active": _won_contract(active, today) if active else None,
    }
    if full:
        payload["contracts"] = [_won_contract(c, today) for c in client.contracts]
    else:
        # 목록은 진행 중 계약 하나만 씁니다. 전체 차수는 상세에서.
        payload["contract_count"] = len(client.contracts)
    return payload


@router.get("/api/ui/won-customers")
def ui_won_customers():
    """수주 고객 목록 + 요약 카드 + 액션 보드 + 수주 전환 대기 — 한 화면이라 한 번에."""
    from datetime import date

    from ...common import won
    from ...common.config import settings as app_settings
    from ...db.models import Client, PendingWon
    from ...db.session import SessionLocal
    from ...integrations.fx import usd_krw_today
    from sqlalchemy.orm import selectinload

    today = date.today()
    try:
        fx = usd_krw_today()
    except Exception:  # 환율을 못 가져와도 목록은 떠야 합니다
        fx = None
    with SessionLocal() as session:
        clients = (
            session.query(Client)
            .options(selectinload(Client.contracts))
            .order_by(Client.company)
            .all()
        )
        rows = [_won_client(client, today, full=False) for client in clients]
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

    # 액션 보드 세 개. 목록을 한 번 더 도는 대신 위에서 만든 payload 를 그대로 씁니다.
    credit_due, pay_due, claims_open = [], [], []
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
        for claim in active["claims"]:
            if claim["progress"] != "조치 완료":
                claims_open.append({
                    "client_id": row["client_id"], "company": row["company"],
                    "kind": claim["kind"], "on": claim["happened_on"],
                    "progress": claim["progress"],
                })
    credit_due.sort(key=lambda x: x["on"] or "9999")
    pay_due.sort(key=lambda x: x["on"] or "9999")

    return {
        "today": today.isoformat(),
        "rows": rows,
        "pending": waiting,
        "boards": {"credit": credit_due, "payment": pay_due, "claim": claims_open},
        # 예상 MRR 환산에 쓰는 환율. **오늘 고시가를 가져옵니다** — 손으로 적던 칸이
        # 있었는데, 그러면 두 사람이 다른 숫자를 보고 회의에 들어가고 그 값이 언제
        # 것인지 아무도 모릅니다. 못 가져오면 설정값으로 떨어집니다.
        "fx_rate": float(fx[0]) if fx else app_settings.MRR_FX_RATE,
        "fx_on": fx[1] if fx else None,
        "fx_source": fx[2] if fx else "설정값",
        "options": {
            "industries": list(won.INDUSTRIES),
            "plans": list(won.PLANS),
            "plan_statuses": list(won.PLAN_STATUSES),
            "deal_types": list(won.DEAL_TYPES),
            "doc_types": list(won.DOC_TYPES),
            "renewal_plans": list(won.RENEWAL_PLANS),
            "claim_progress": list(won.CLAIM_PROGRESS),
            "payment_methods": list(won.PAYMENT_METHODS),
            "payment_types": list(won.PAYMENT_TYPES),
            "currencies": list(won.CURRENCIES),
            "customer_types": list(won.ALLOCATABLE_BANDS),
            "departments": ["GTM", "Interactive", "AX"],
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
                selectinload(Client.contracts).selectinload(ClientContract.claims),
            )
            .filter(Client.client_id == client_id)
            .one_or_none()
        )
        if client is None:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        payload = _won_client(client, today, full=True)
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
