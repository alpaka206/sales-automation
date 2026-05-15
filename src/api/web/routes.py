"""Web UI routes — serves Jinja2 templates for the operator dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from ...agents.approval import ApprovalError, approve, reject
from ...common.config import settings
from ...db.models import (
    Conversation, ICPRule, KnowledgeDocument,
    LLMUsage, Message, OutboundIntent, Prospect,
)
from ...db.session import SessionLocal
from ...llm.knowledge import reset_cache as _reset_kb_cache

_TEMPLATE_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
router = APIRouter(tags=["web"])

_TRACKED_STATUSES = ("pending_approval", "approved", "sent", "bounced", "replied")


def _dashboard_context() -> dict:
    """Query DB for dashboard data."""
    with SessionLocal() as session:
        recent = (
            session.execute(
                select(Message, Conversation.topic)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .order_by(Message.created_at.desc())
                .limit(20)
            )
            .all()
        )
        recent_messages = [
            {
                "id": msg.id,
                "status": msg.status,
                "category": topic or "-",
                "subject": msg.subject or "(제목 없음)",
                "channel": msg.channel,
                "direction": msg.direction,
                "created_at": msg.created_at,
            }
            for msg, topic in recent
        ]

        status_rows = session.execute(
            select(Message.status, func.count()).group_by(Message.status)
        ).all()
        status_counts = {s: 0 for s in _TRACKED_STATUSES}
        for status, cnt in status_rows:
            status_counts[status] = cnt

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_sent = session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.status == "sent", Message.sent_at >= today_start)
        ) or 0

        category_rows = session.execute(
            select(Conversation.topic, func.count())
            .join(Message, Conversation.id == Message.conversation_id)
            .where(Conversation.topic.isnot(None))
            .group_by(Conversation.topic)
            .order_by(func.count().desc())
        ).all()
        category_counts = [(cat or "기타", cnt) for cat, cnt in category_rows]

    return {
        "recent_messages": recent_messages,
        "status_counts": status_counts,
        "today_sent": today_sent,
        "daily_limit": settings.DAILY_SEND_LIMIT,
        "category_counts": category_counts,
    }


@router.get("/")
async def dashboard(request: Request):
    """Main dashboard — recent messages, status counts, daily stats."""
    ctx = _dashboard_context()
    return templates.TemplateResponse(request, "dashboard.html", ctx)


def _message_detail_context(message_id: int) -> dict:
    """Load a single message with related contact/prospect data."""
    with SessionLocal() as session:
        msg = session.execute(
            select(Message)
            .options(
                joinedload(Message.conversation).joinedload(Conversation.contact),
                joinedload(Message.conversation).joinedload(Conversation.prospect),
            )
            .where(Message.id == message_id)
        ).unique().scalar_one_or_none()
        if not msg:
            return {}

        conv = msg.conversation
        contact = conv.contact if conv else None
        prospect = conv.prospect if conv else None

        return {
            "msg": {
                "id": msg.id,
                "status": msg.status,
                "subject": msg.subject or "",
                "body": msg.body,
                "channel": msg.channel,
                "direction": msg.direction,
                "language": msg.language,
                "to_address": msg.to_address or "",
                "from_address": msg.from_address or "",
                "score_snapshot": msg.score_snapshot,
                "scheduled_at": msg.scheduled_at,
                "sent_at": msg.sent_at,
                "created_at": msg.created_at,
                "category": conv.topic if conv else "-",
            },
            "contact": {
                "id": contact.id,
                "name": contact.full_name,
                "email": contact.email,
                "company": contact.company,
            } if contact else None,
            "prospect": {
                "id": prospect.id,
                "name": prospect.full_name,
                "email": prospect.email,
                "company": prospect.company,
                "icp_score": prospect.icp_score,
            } if prospect else None,
        }


@router.get("/messages/{message_id}")
async def message_detail(request: Request, message_id: int):
    """Message detail page with editable body and send/reject actions."""
    ctx = _message_detail_context(message_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="메시지를 찾을 수 없습니다")
    return templates.TemplateResponse(request, "message_detail.html", ctx)


@router.post("/messages/{message_id}/send")
async def message_send(message_id: int, body: str = Form(""), subject: str = Form("")):
    """Approve (and optionally edit) a message for sending."""
    try:
        edited = body.strip() if body.strip() else None
        approve(message_id, approver="web_ui", edited_body=edited)
    except ApprovalError as exc:
        return HTMLResponse(
            f'<div class="text-red-600 text-sm">{exc}</div>', status_code=400
        )
    return HTMLResponse(
        '<div class="text-green-600 text-sm font-medium">승인 완료 — 발송 대기 중</div>'
    )


@router.post("/messages/{message_id}/reject")
async def message_reject(message_id: int, reason: str = Form("")):
    """Reject a message with an optional reason."""
    try:
        reject(message_id, approver="web_ui", reason=reason.strip() or None)
    except ApprovalError as exc:
        return HTMLResponse(
            f'<div class="text-red-600 text-sm">{exc}</div>', status_code=400
        )
    return HTMLResponse(
        '<div class="text-orange-600 text-sm font-medium">거절 처리 완료</div>'
    )


@router.post("/messages/{message_id}/edit")
async def message_edit(message_id: int, body: str = Form(""), subject: str = Form("")):
    """Save edits to a pending message without sending."""
    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        if not msg:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">메시지를 찾을 수 없습니다</div>',
                status_code=404,
            )
        if msg.status != "pending_approval":
            return HTMLResponse(
                f'<div class="text-red-600 text-sm">편집 불가 (현재 상태: {msg.status})</div>',
                status_code=400,
            )
        if body.strip():
            msg.body = body.strip()
        if subject.strip():
            msg.subject = subject.strip()
        session.commit()
    return HTMLResponse(
        '<div class="text-blue-600 text-sm font-medium">저장 완료</div>'
    )


# ---------- Knowledge base CRUD ----------


def _slugify(title: str) -> str:
    """Simple slug from Korean/English title."""
    import re
    slug = title.lower().strip().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9가-힣\-]", "", slug)
    return slug or "untitled"


@router.get("/knowledge")
async def knowledge_list(request: Request):
    """List all knowledge base documents."""
    with SessionLocal() as session:
        docs = (
            session.query(KnowledgeDocument)
            .order_by(KnowledgeDocument.updated_at.desc())
            .all()
        )
        items = [
            {
                "id": d.id,
                "title": d.title,
                "slug": d.slug,
                "categories": d.categories or [],
                "scope": d.scope,
                "updated_at": d.updated_at,
            }
            for d in docs
        ]
    return templates.TemplateResponse(request, "knowledge_list.html", {"docs": items})


@router.get("/knowledge/new")
async def knowledge_new(request: Request):
    """Form to create a new knowledge document."""
    return templates.TemplateResponse(request, "knowledge_form.html", {
        "doc": None, "mode": "create",
    })


@router.get("/knowledge/{doc_id}")
async def knowledge_edit(request: Request, doc_id: int):
    """Edit form for an existing knowledge document."""
    with SessionLocal() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")
        item = {
            "id": doc.id,
            "title": doc.title,
            "slug": doc.slug,
            "categories": ",".join(doc.categories) if doc.categories else "",
            "scope": doc.scope,
            "body": doc.body,
        }
    return templates.TemplateResponse(request, "knowledge_form.html", {
        "doc": item, "mode": "edit",
    })


@router.post("/knowledge")
async def knowledge_create(
    title: str = Form(""),
    categories: str = Form(""),
    scope: str = Form("both"),
    body: str = Form(""),
):
    """Create a new knowledge document."""
    if not title.strip() or not body.strip():
        return HTMLResponse(
            '<div class="text-red-600 text-sm">제목과 본문은 필수입니다</div>',
            status_code=400,
        )
    cats = [c.strip() for c in categories.split(",") if c.strip()] or None
    slug = _slugify(title.strip())
    with SessionLocal() as session:
        existing = session.query(KnowledgeDocument).filter_by(slug=slug).first()
        if existing:
            slug = f"{slug}-{existing.id + 1}"
        doc = KnowledgeDocument(
            title=title.strip(), slug=slug, categories=cats,
            scope=scope, body=body.strip(),
        )
        session.add(doc)
        session.commit()
    _reset_kb_cache()
    return HTMLResponse(
        '<div class="text-green-600 text-sm font-medium">문서 생성 완료</div>'
        '<script>setTimeout(()=>location.href="/knowledge",500)</script>'
    )


@router.put("/knowledge/{doc_id}")
async def knowledge_update(
    doc_id: int,
    title: str = Form(""),
    categories: str = Form(""),
    scope: str = Form("both"),
    body: str = Form(""),
):
    """Update an existing knowledge document."""
    cats = [c.strip() for c in categories.split(",") if c.strip()] or None
    with SessionLocal() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        if not doc:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">문서를 찾을 수 없습니다</div>',
                status_code=404,
            )
        if title.strip():
            doc.title = title.strip()
        doc.categories = cats
        doc.scope = scope
        if body.strip():
            doc.body = body.strip()
        session.commit()
    _reset_kb_cache()
    return HTMLResponse(
        '<div class="text-green-600 text-sm font-medium">저장 완료</div>'
    )


@router.delete("/knowledge/{doc_id}")
async def knowledge_delete(doc_id: int):
    """Delete a knowledge document."""
    with SessionLocal() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        if not doc:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">문서를 찾을 수 없습니다</div>',
                status_code=404,
            )
        session.delete(doc)
        session.commit()
    _reset_kb_cache()
    return HTMLResponse(
        '<div class="text-orange-600 text-sm font-medium">삭제 완료</div>'
        '<script>setTimeout(()=>location.href="/knowledge",500)</script>'
    )


# ---------- ICP Rules ----------

_DEFAULT_SOURCES = ("youtube", "linkedin_comments", "google_search", "job_board", "manual_csv")


@router.get("/icp-rules")
async def icp_rules_list(request: Request):
    """List ICP scoring rules by source."""
    with SessionLocal() as session:
        rules = session.query(ICPRule).order_by(ICPRule.source).all()
        items = {r.source: {"id": r.id, "source": r.source, "enabled": r.enabled,
                            "criteria_md": r.criteria_md, "updated_at": r.updated_at} for r in rules}
    all_sources = []
    for s in _DEFAULT_SOURCES:
        if s in items:
            all_sources.append(items[s])
        else:
            all_sources.append({"id": None, "source": s, "enabled": False, "criteria_md": "", "updated_at": None})
    for s, r in items.items():
        if s not in _DEFAULT_SOURCES:
            all_sources.append(r)
    return templates.TemplateResponse(request, "icp_rules_list.html", {"rules": all_sources})


@router.get("/icp-rules/{source}/edit")
async def icp_rules_edit(request: Request, source: str):
    """Edit form for a source's ICP criteria."""
    with SessionLocal() as session:
        rule = session.query(ICPRule).filter_by(source=source).first()
        item = {
            "source": source,
            "criteria_md": rule.criteria_md if rule else "",
            "enabled": rule.enabled if rule else True,
        }
    return templates.TemplateResponse(request, "icp_rules_form.html", {"rule": item})


@router.post("/icp-rules/{source}")
async def icp_rules_save(
    source: str,
    criteria_md: str = Form(""),
    enabled: str = Form("on"),
):
    """Create or update ICP criteria for a source."""
    is_enabled = enabled in ("on", "true", "1")
    with SessionLocal() as session:
        rule = session.query(ICPRule).filter_by(source=source).first()
        if rule:
            rule.criteria_md = criteria_md.strip()
            rule.enabled = is_enabled
        else:
            rule = ICPRule(source=source, criteria_md=criteria_md.strip(), enabled=is_enabled)
            session.add(rule)
        session.commit()
    return HTMLResponse(
        '<div class="text-green-600 text-sm font-medium">저장 완료</div>'
    )


# ---------- Outbound intake ----------


@router.get("/outbound/new")
async def outbound_new(request: Request):
    """Natural language outbound intake form."""
    return templates.TemplateResponse(request, "outbound_new.html")


@router.post("/outbound/run-intent")
async def outbound_run_intent(query: str = Form("")):
    """Route a natural-language query to the outbound dispatcher."""
    if not query.strip():
        return HTMLResponse(
            '<div class="text-red-600 text-sm">검색어를 입력해주세요</div>',
            status_code=400,
        )
    from ...agents.outbound.dispatcher import dispatch_natural_query
    from ...llm.client import LLMClient

    llm = LLMClient()
    result = dispatch_natural_query(llm, query.strip())
    status = result.get("status", "unknown")

    if status == "rejected":
        return HTMLResponse(
            f'<div class="text-orange-600 text-sm">'
            f'신뢰도 부족 ({result.get("confidence", 0):.0%}): {result.get("rationale", "")}</div>'
        )
    if status == "pending_user_input":
        fields = result.get("requires_user_input", [])
        return HTMLResponse(
            f'<div class="text-yellow-600 text-sm">추가 정보 필요: {", ".join(fields)}</div>'
        )
    if status == "dispatched":
        return HTMLResponse(
            f'<div class="text-green-600 text-sm font-medium">'
            f'발굴 완료 ({result.get("source", "")}) — '
            f'<a href="/prospects" class="underline">결과 보기</a></div>'
        )
    return HTMLResponse(f'<div class="text-gray-500 text-sm">상태: {status}</div>')


@router.get("/outbound/intents/{intent_id}")
async def outbound_intent_detail(request: Request, intent_id: int):
    """View a single outbound intent's status and details."""
    with SessionLocal() as session:
        intent = session.get(OutboundIntent, intent_id)
        if not intent:
            raise HTTPException(status_code=404, detail="인텐트를 찾을 수 없습니다")
        item = {
            "id": intent.id,
            "user_query": intent.user_query,
            "routed_source": intent.routed_source,
            "routed_filters": intent.routed_filters,
            "confidence": intent.confidence,
            "status": intent.status,
            "created_at": intent.created_at,
        }
    return templates.TemplateResponse(request, "outbound_intent.html", {"intent": item})


@router.get("/prospects")
async def prospects_list(request: Request):
    """List all prospects with optional filters."""
    source = request.query_params.get("source", "")
    status_filter = request.query_params.get("status", "")
    q = select(Prospect).order_by(Prospect.created_at.desc()).limit(100)
    if source:
        q = q.where(Prospect.source == source)
    if status_filter:
        q = q.where(Prospect.status == status_filter)
    with SessionLocal() as session:
        rows = session.execute(q).scalars().all()
        items = [
            {
                "id": p.id,
                "full_name": p.full_name,
                "email": p.email or "-",
                "company": p.company or "-",
                "source": p.source,
                "icp_score": p.icp_score,
                "status": p.status,
                "country": p.country or "-",
                "created_at": p.created_at,
            }
            for p in rows
        ]
    return templates.TemplateResponse(request, "prospects_list.html", {
        "prospects": items, "filter_source": source, "filter_status": status_filter,
    })


@router.post("/prospects/bulk-approve")
async def prospects_bulk_approve(request: Request):
    """Approve all selected prospects' pending messages."""
    form = await request.form()
    ids = [int(v) for k, v in form.multi_items() if k == "prospect_id"]
    if not ids:
        return HTMLResponse(
            '<div class="text-red-600 text-sm">선택된 프로스펙트가 없습니다</div>',
            status_code=400,
        )
    approved_count = 0
    with SessionLocal() as session:
        for pid in ids:
            prospect = session.get(Prospect, pid)
            if not prospect or not prospect.contact_id:
                continue
            msgs = (
                session.query(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .filter(
                    Conversation.prospect_id == pid,
                    Message.status == "pending_approval",
                )
                .all()
            )
            for msg in msgs:
                try:
                    approve(msg.id, approver="web_ui_bulk")
                    approved_count += 1
                except Exception:
                    pass
    return HTMLResponse(
        f'<div class="text-green-600 text-sm font-medium">{approved_count}건 승인 완료</div>'
    )


# ---------- Settings ----------


def _mask_value(key: str, val: str) -> str:
    """Mask sensitive env var values."""
    if not val:
        return "(미설정)"
    sensitive = ("token", "key", "secret", "password", "cookie")
    if any(s in key.lower() for s in sensitive):
        return val[:4] + "***" if len(val) > 4 else "***"
    return val


def _settings_context() -> dict:
    """Build settings page data: healthcheck, env vars, LLM usage."""
    from ...common.healthcheck import run_healthchecks

    report = run_healthchecks()

    env_vars = []
    for field_name, field_info in settings.model_fields.items():
        val = str(getattr(settings, field_name, ""))
        env_vars.append({
            "name": field_name,
            "value": _mask_value(field_name, val),
        })

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start.replace(hour=0)
    from datetime import timedelta
    week_start = today_start - timedelta(days=today_start.weekday())

    try:
        with SessionLocal() as session:
            today_llm = session.scalar(
                select(func.count()).select_from(LLMUsage).where(LLMUsage.created_at >= today_start)
            ) or 0
            week_llm = session.scalar(
                select(func.count()).select_from(LLMUsage).where(LLMUsage.created_at >= week_start)
            ) or 0
    except Exception:
        today_llm = 0
        week_llm = 0

    claude_cli_ok = any(c.name == "Claude CLI" and c.status == "PASS" for c in report.checks)

    return {
        "checks": [c.model_dump() for c in report.checks],
        "overall_status": report.overall_status,
        "env_vars": env_vars,
        "today_llm": today_llm,
        "week_llm": week_llm,
        "claude_cli_ok": claude_cli_ok,
    }


@router.get("/settings")
async def settings_page(request: Request):
    """System settings, health checks, and env vars."""
    ctx = _settings_context()
    return templates.TemplateResponse(request, "settings.html", ctx)


@router.post("/settings/refresh-healthcheck")
async def settings_refresh_healthcheck():
    """Re-run health checks and return updated HTML."""
    from ...common.healthcheck import run_healthchecks

    report = run_healthchecks()
    rows = ""
    for c in report.checks:
        color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}.get(c.status, "gray")
        rows += (
            f'<tr class="border-b"><td class="px-4 py-2 text-sm">{c.name}</td>'
            f'<td class="px-4 py-2"><span class="text-xs font-medium text-{color}-700 '
            f'bg-{color}-100 px-2 py-0.5 rounded">{c.status}</span></td>'
            f'<td class="px-4 py-2 text-xs text-gray-500">{c.detail}</td>'
            f'<td class="px-4 py-2 text-xs text-gray-400">{c.latency_ms}ms</td></tr>'
        )
    return HTMLResponse(rows)


# ---------- Unsubscribe ----------


@router.get("/unsubscribe")
async def unsubscribe(request: Request, email: str = "", token: str = ""):
    """Handle unsubscribe link clicks."""
    from ...integrations.compliance import suppress_email, verify_unsub_token

    if not email or not token:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;padding:40px;text-align:center'>"
            "<h2>잘못된 요청입니다.</h2></body></html>",
            status_code=400,
        )
    if not verify_unsub_token(email, token):
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;padding:40px;text-align:center'>"
            "<h2>유효하지 않은 링크입니다.</h2></body></html>",
            status_code=400,
        )
    suppress_email(email, reason="unsubscribe")
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;padding:40px;text-align:center'>"
        "<h2>수신 거부 처리가 완료되었습니다.</h2>"
        f"<p>{email} 주소로 더 이상 메일을 보내지 않습니다.</p>"
        "</body></html>"
    )
