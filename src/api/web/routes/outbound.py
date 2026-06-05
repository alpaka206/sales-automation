"""Outbound intake (natural-language) + prospects web routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from ....agents.approval import approve
from ....db.models import Conversation, Message, OutboundIntent, Prospect
from ....db.session import SessionLocal
from ..auth import actor_name
from ._shared import esc, templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])


@router.get("/outbound/new")
async def outbound_new(request: Request):
    """Natural language outbound intake form."""
    return templates.TemplateResponse(request, "outbound_new.html")


@router.post("/outbound/run-intent")
async def outbound_run_intent(query: str = Form("")):
    """Route a natural-language query and QUEUE it for the local outbound worker.

    The crawl itself runs on a local machine (Playwright + CPU), not on the deployed
    instance — see ``agents/outbound/dispatcher.route_and_enqueue`` and
    ``scripts/run_outbound_worker.py``. The web only routes (cheap Gemini call) and parks
    a ``queued`` intent; the local worker polls the shared DB and executes it.
    """
    if not query.strip():
        return HTMLResponse(
            '<div class="banner banner--danger" style="padding:10px 12px">검색어를 입력해주세요</div>',
            status_code=400,
        )
    from ....agents.outbound.dispatcher import route_and_enqueue
    from ....llm.client import LLMClient

    llm = LLMClient()
    result = route_and_enqueue(llm, query.strip())
    status = result.get("status", "unknown")

    if status == "rejected":
        return HTMLResponse(
            f'<div class="banner banner--warn" style="padding:10px 12px">'
            f'<div><span class="banner__title">신뢰도 부족 ({result.get("confidence", 0):.0%})</span>'
            f'<div class="banner__body">{esc(result.get("rationale", ""))}</div></div></div>'
        )
    if status == "pending_user_input":
        fields = ", ".join(result.get("requires_user_input", []))
        iid = result.get("intent_id")
        return HTMLResponse(
            f'<div class="banner banner--warn" style="padding:10px 12px">'
            f'<div><span class="banner__title">추가 정보 필요</span>'
            f'<div class="banner__body">{esc(fields)} — '
            f'<a href="/outbound/intents/{iid}" style="color:var(--accent)">인텐트 #{iid}</a></div></div></div>'
        )
    if status == "queued":
        iid = result.get("intent_id")
        src = esc(str(result.get("routed_source", "")))
        return HTMLResponse(
            f'<div class="banner banner--ok" style="padding:10px 12px">'
            f'<div><span class="banner__title">발굴 대기열에 등록됨 (소스: {src})</span>'
            f'<div class="banner__body">로컬 아웃바운드 워커가 이 인텐트를 실행합니다. '
            f'<a href="/outbound/intents/{iid}" style="color:var(--accent)">인텐트 #{iid}</a> · '
            f'<a href="/prospects" style="color:var(--accent)">프로스펙트</a></div></div></div>'
        )
    return HTMLResponse(f'<div class="banner" style="padding:10px 12px">상태: {esc(status)}</div>')


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


# Only these prospect statuses are eligible for (bulk) send approval — mirrors the
# web UI's eligible-checkbox gate and the spec ("발송 자격은 collected/analyzed/queued만").
_BULK_ELIGIBLE_STATUSES = ("collected", "analyzed", "queued")


@router.get("/prospects")
async def prospects_list(request: Request):
    """List all prospects with optional filters (source, status, min ICP score)."""
    source = request.query_params.get("source", "")
    status_filter = request.query_params.get("status", "")
    min_icp_raw = request.query_params.get("min_icp", "")
    try:
        min_icp = max(0, min(100, int(min_icp_raw))) if min_icp_raw != "" else 0
    except ValueError:
        min_icp = 0
    q = select(Prospect).order_by(Prospect.created_at.desc()).limit(100)
    if source:
        q = q.where(Prospect.source == source)
    if status_filter:
        q = q.where(Prospect.status == status_filter)
    if min_icp > 0:
        q = q.where(Prospect.icp_score >= min_icp)
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
        "filter_min_icp": min_icp,
    })


@router.post("/prospects/bulk-approve")
async def prospects_bulk_approve(request: Request):
    """Approve selected prospects' pending messages.

    Honors the spec's "how many people, to whom, up to what score" gate: a prospect is
    only approved when its status is send-eligible AND its ICP score is >= the submitted
    ``min_icp`` floor. Anything below the floor (or ineligible) is skipped and reported,
    so the operator's "최저 N점까지" choice is enforced server-side, not just in the UI.
    """
    form = await request.form()
    ids = [int(v) for k, v in form.multi_items() if k == "prospect_id"]
    try:
        min_icp = max(0, min(100, int(form.get("min_icp") or 0)))
    except (ValueError, TypeError):
        min_icp = 0
    if not ids:
        return HTMLResponse(
            '<div class="banner banner--danger" style="padding:10px 12px">선택된 프로스펙트가 없습니다</div>',
            status_code=400,
        )
    approved_count = 0
    skipped_below = 0
    skipped_ineligible = 0
    with SessionLocal() as session:
        for pid in ids:
            prospect = session.get(Prospect, pid)
            if not prospect or not prospect.contact_id:
                continue
            if prospect.status not in _BULK_ELIGIBLE_STATUSES:
                skipped_ineligible += 1
                continue
            if min_icp and (prospect.icp_score is None or prospect.icp_score < min_icp):
                skipped_below += 1
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
                    approve(msg.id, approver=actor_name(request, fallback="web_ui_bulk"))
                    approved_count += 1
                except Exception:
                    logger.warning("Failed to approve message %d in bulk", msg.id, exc_info=True)
    notes = []
    if skipped_below:
        notes.append(f"{skipped_below}명 점수 미달(≥{min_icp})")
    if skipped_ineligible:
        notes.append(f"{skipped_ineligible}명 자격 없음")
    note_html = f' <span class="t-subtle">· {", ".join(notes)} 제외</span>' if notes else ""
    return HTMLResponse(
        f'<div class="banner banner--ok" style="padding:10px 12px">'
        f'<span class="banner__title">{approved_count}건 승인 완료</span>{note_html}</div>'
    )
