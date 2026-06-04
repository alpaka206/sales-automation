"""Outbound intake (natural-language) + prospects web routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from ....agents.approval import approve
from ....db.models import Conversation, Message, OutboundIntent, Prospect
from ....db.session import SessionLocal
from ._shared import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])


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
    from ....agents.outbound.dispatcher import dispatch_natural_query
    from ....llm.client import LLMClient

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
                    logger.warning("Failed to approve message %d in bulk", msg.id, exc_info=True)
    return HTMLResponse(
        f'<div class="text-green-600 text-sm font-medium">{approved_count}건 승인 완료</div>'
    )
