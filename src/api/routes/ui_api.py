"""JSON for the React screens.

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
from ._shared import external_url

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
async def ui_dashboard(_request: Request):
    from .dashboard import _dashboard_context

    context = _dashboard_context()
    return {
        "queue": context["recent_messages"],
        "now": context["now"],
        "counters": {
            "received_today": context["received_today"],
            "awaiting_total": context["awaiting_total"],
            "awaiting_new": context["awaiting_new"],
            "awaiting_negotiation": context["awaiting_negotiation"],
        },
        "stage_labels": context["stage_labels"],
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
async def ui_overview():
    """전체 대시보드. A roll-up of screens that each own their own numbers."""
    from .dashboard import _overview_context

    return _overview_context()


@router.get("/api/ui/contracts")
async def ui_contracts(status: str = "", q: str = ""):
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
async def ui_messages(status: str = "awaiting", stage: str = "", sort: str = "oldest"):
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

    context = _message_detail_context(message_id)
    if not context:
        raise HTTPException(status_code=404, detail="메시지를 찾을 수 없습니다")
    await _translate_inbound_bubbles(context)
    context["stage_labels"] = {key: label for key, label, _ in PIPELINE_STAGES}
    return context


@router.get("/api/ui/customers")
async def ui_customers(stage: str = "", q: str = ""):
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
async def ui_operations(period: str = "month"):
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
async def ui_company(domain: str):
    """회사 상세. Personal domains are never grouped — the route already refuses, and
    this returns exactly what it decided rather than deciding again."""
    from .companies import company_context

    return company_context(domain)


@router.get("/api/ui/settings/users")
async def ui_settings_users(request: Request):
    """접근 승인. Admin-gated by the SAME check the page uses (settings_page.is_admin —
    which is NOT logs.py's admin_required; they disagree in basic mode)."""
    from ..auth import is_admin

    if not is_admin(request):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    from .settings_page import settings_users

    response = await settings_users(request)
    return {key: value for key, value in response.context.items() if key != "request"}


@router.get("/api/ui/recovery")
async def ui_recovery(request: Request):
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
async def ui_logs(request: Request, view: str = "all"):
    """운영 로그. Gated by the SAME function the page uses, not by a second one that
    happens to look similar — a JSON copy of a screen must not be the way around that
    screen's gate, and it must not refuse what the screen allows either.

    (There are two admin checks in this app: ``admin_required`` here and in logs.py, and
    ``is_admin`` in settings_page.py. They disagree in basic mode, which is why 접근 승인
    is unreachable there while 운영 로그 is not.)
    """
    from ..auth import admin_required
    from .logs import _events

    if not admin_required(request):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    return {"rows": _events(view)}


@router.get("/api/ui/customers/{contact_id}")
async def ui_customer_detail(contact_id: int):
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
    # scripts/sync_notion_local.py; editing a copy here would be undone by the next sync.
    ("policy", "정책 문서"),
)


def _template_kind(key: str) -> str:
    return "signature" if key.startswith("signature_") else "template"


@router.get("/api/ui/email-templates")
async def ui_email_templates():
    """Grouped, not one flat list — and each group says what it is for."""
    from ...db.models import EmailTemplate
    from ...db.session import SessionLocal

    from ...db.models import PolicySource

    with SessionLocal() as session:
        policy_count = session.query(PolicySource).count()
        rows = session.query(EmailTemplate).order_by(EmailTemplate.updated_at.desc()).all()
        items = [
            {
                "id": row.id,
                "name": row.name,
                "language": row.language or "all",
                "updated_at": row.updated_at,
                "kind": _template_kind(row.key),
                "chars": len(row.body or ""),
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
async def quote_policy() -> dict:
    """The tier table the 견적 계산기 screen prices against.

    Behind the console's auth gate rather than on /static because the policy is internal
    sales data — `tier_to_client_dict` already strips contribution margin, and the gate
    keeps the rest of it off the public mount.
    """
    from ...common.quote_tiers import policy_client

    return policy_client()


@router.get("/api/ui/email-templates/{template_id}")
async def ui_email_template(template_id: int):
    from ...db.models import EmailTemplate
    from ...db.session import SessionLocal

    with SessionLocal() as session:
        row = session.get(EmailTemplate, template_id)
        if not row:
            raise HTTPException(status_code=404, detail="템플릿을 찾을 수 없습니다")
        return {
            "id": row.id,
            "name": row.name,
            "language": row.language or "all",
            "body": row.body,
            "kind": _template_kind(row.key),
        }


@router.get("/api/ui/policy-docs")
async def ui_policy_docs():
    """정책 문서 — registrations AND the synced copy.

    Read-only on purpose: policy is owned in Notion and pulled in by
    ``scripts/sync_notion_local.py``. What the screen was missing is the ability to SEE
    what got pulled — without it the only way to check a document was to open Notion and
    compare by eye, which is the manual copying this feature exists to remove.
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
                    # The screen renders this straight into an href, so a stored
                    # "javascript:" would execute in the console. Sanitised here, once,
                    # rather than in the component that happens to link it.
                    "notion_url": external_url(row.notion_url),
                    "mode": row.mode,
                    "status": row.status,
                    "body": row.body,
                    "chars": len(row.body or ""),
                    "last_synced_at": row.last_synced_at,
                    "last_error": row.last_error,
                    "from_file": not (row.notion_url or "").strip(),
                }
                for row in rows
            ],
        }


@router.get("/api/ui/pipeline/{stage}/cards")
async def ui_pipeline_page(stage: str, offset: int = 0):
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
