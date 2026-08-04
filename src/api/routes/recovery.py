"""Operator recovery console for durable inbound and delivery failures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import joinedload

from ...db.models import Conversation, Event, InboundJob, Message
from ...db.session import SessionLocal
from ..auth import actor_name

router = APIRouter(tags=["web"])


def _audit(session, request: Request, action: str, object_type: str, object_id: int) -> None:
    session.add(
        Event(
            kind="operator_recovery",
            payload={
                "actor": actor_name(request, fallback="local_operator"),
                "action": action,
                "object_type": object_type,
                "object_id": object_id,
            },
        )
    )


def recovery_context() -> dict:
    """The four durable failure lists, for whoever renders them.

    Lives here next to the retry/resolve actions that operate on the same rows; the
    /logs page imports it so both tabs of the operations screen come from one query.
    """
    stale_before = datetime.now(timezone.utc) - timedelta(minutes=30)
    with SessionLocal() as session:
        inbound_jobs = session.scalars(
            select(InboundJob)
            .where(InboundJob.status == "dead")
            .order_by(InboundJob.updated_at.desc())
            .limit(100)
        ).all()
        messages = session.scalars(
            select(Message)
            .options(joinedload(Message.conversation).joinedload(Conversation.contact))
            .where(Message.status.in_(["send_failed", "delivery_unknown", "draft_failed"]))
            .order_by(Message.created_at.desc())
            .limit(100)
        ).unique().all()
        stale_drafts = session.scalars(
            select(Message)
            .options(joinedload(Message.conversation).joinedload(Conversation.contact))
            .where(Message.status == "drafting", Message.created_at <= stale_before)
            .order_by(Message.created_at)
            .limit(100)
        ).unique().all()
        sync_failures = session.scalars(
            select(Message)
            .options(joinedload(Message.conversation).joinedload(Conversation.contact))
            .where(
                Message.status == "sent",
                Message.post_send_synced_at.is_(None),
                Message.post_send_sync_error.is_not(None),
            )
            .order_by(Message.post_send_sync_attempted_at.desc())
            .limit(100)
        ).unique().all()
    return {
        "inbound_jobs": inbound_jobs,
        "messages": messages,
        "stale_drafts": stale_drafts,
        "sync_failures": sync_failures,
    }


def recovery_pending_count(context: dict) -> int:
    """How many rows actually need an operator. Stale drafts are informational."""
    return sum(
        len(context[key]) for key in ("messages", "inbound_jobs", "sync_failures")
    )


@router.post("/operations/recovery/hubspot-sync")
async def hubspot_reconcile(request: Request, apply: str = Form("")):
    """최신화 — ask HubSpot about every thread we are still holding an answer for.

    The drift this exists for: a ticket answered, moved or deleted in HubSpot while our
    queue still shows an unsent draft, so the operator is asked to send a reply the
    customer already has — or one for a thread that no longer exists.

    Read-mostly. It sends nothing and writes nothing to HubSpot; the only local writes
    retire drafts and close threads whose ticket is gone.
    """
    from ...agents.hubspot_reconcile import reconcile_with_hubspot

    # apply=false unless the operator confirmed. Stage moves land either way; retiring a
    # draft on a 404 does not, because 404 is also what a ticket id from another portal
    # or a backfilled row looks like.
    report = await run_in_threadpool(reconcile_with_hubspot, apply=apply == "true")
    with SessionLocal() as session:
        _audit(session, request, "hubspot_reconcile", "conversation", report["retired"])
        session.commit()
    return report


@router.post("/operations/recovery/clear-failures")
async def clear_send_failures(request: Request):
    """Empty the 발송·작성 실패 list.

    Retires rather than deletes: `rejected` is the status the review screen already uses
    for "this draft will not be sent", so the row leaves every queue while the thread
    keeps its history. A failure worth investigating should be investigated before this
    is pressed — that is why it is a button an operator chooses, not a sweep on a timer.
    """
    with SessionLocal() as session:
        rows = session.scalars(
            select(Message).where(
                Message.status.in_(["send_failed", "draft_failed", "delivery_unknown"])
            )
        ).all()
        for row in rows:
            row.status = "rejected"
        _audit(session, request, "clear_send_failures", "message", len(rows))
        session.commit()
    return {"cleared": len(rows)}


@router.get("/operations/recovery")
async def recovery_console_redirect():
    """The console moved into /logs; keep old links and bookmarks working."""
    return RedirectResponse("/logs?tab=recovery", status_code=308)


@router.post("/operations/recovery/inbound/{job_id}/retry")
async def retry_inbound_job(request: Request, job_id: int):
    with SessionLocal() as session:
        result = session.execute(
            update(InboundJob)
            .where(InboundJob.id == job_id, InboundJob.status == "dead")
            .values(
                status="pending",
                attempts=0,
                available_at=datetime.now(timezone.utc),
                locked_at=None,
                locked_by=None,
                completed_at=None,
                last_error=None,
            )
        )
        if result.rowcount != 1:
            raise HTTPException(status_code=409, detail="재처리할 수 없는 작업 상태입니다")
        _audit(session, request, "retry", "inbound_job", job_id)
        session.commit()
    return RedirectResponse("/logs?tab=recovery&updated=inbound", status_code=303)


@router.post("/operations/recovery/messages/{message_id}/retry")
async def retry_failed_message(request: Request, message_id: int):
    with SessionLocal() as session:
        result = session.execute(
            update(Message)
            .where(Message.id == message_id, Message.status == "send_failed")
            .values(
                status="approved",
                scheduled_at=datetime.now(timezone.utc),
                send_claimed_at=None,
            )
        )
        if result.rowcount != 1:
            raise HTTPException(status_code=409, detail="발송 실패 상태만 재시도할 수 있습니다")
        _audit(session, request, "retry", "message", message_id)
        session.commit()
    return RedirectResponse("/logs?tab=recovery&updated=message", status_code=303)


@router.post("/operations/recovery/messages/{message_id}/resolve")
async def resolve_unknown_delivery(
    request: Request,
    message_id: int,
    action: str = Form(...),
):
    if action not in {"confirmed_sent", "confirmed_not_sent"}:
        raise HTTPException(status_code=400, detail="지원하지 않는 확인 결과입니다")
    with SessionLocal() as session:
        message = session.get(Message, message_id)
        if not message or message.status != "delivery_unknown":
            raise HTTPException(status_code=409, detail="발송 확인 필요 상태가 아닙니다")
        if action == "confirmed_not_sent":
            message.status = "approved"
            message.scheduled_at = datetime.now(timezone.utc)
            message.send_claimed_at = None
        else:
            now = datetime.now(timezone.utc)
            message.status = "sent"
            message.sent_at = message.sent_at or now
            message.send_claimed_at = None
            message.post_send_synced_at = None
            message.post_send_sync_attempts = 0
            message.post_send_sync_attempted_at = None
            message.post_send_sync_error = "operator_confirmed_sent"
            conversation = session.get(Conversation, message.conversation_id)
            if conversation and message.prompt_variant != "auto_ack":
                conversation.last_outgoing_at = message.sent_at
                conversation.stage = "meeting_link_sent"
        _audit(session, request, action, "message", message_id)
        session.commit()
    return RedirectResponse("/logs?tab=recovery&updated=delivery", status_code=303)


@router.post("/operations/recovery/messages/{message_id}/sync")
async def retry_message_sync(request: Request, message_id: int):
    with SessionLocal() as session:
        result = session.execute(
            update(Message)
            .where(Message.id == message_id, Message.status == "sent")
            .values(
                post_send_synced_at=None,
                post_send_sync_attempts=0,
                post_send_sync_attempted_at=None,
                post_send_sync_error="operator_retry",
            )
        )
        if result.rowcount != 1:
            raise HTTPException(status_code=409, detail="발송 완료된 메시지만 동기화할 수 있습니다")
        _audit(session, request, "retry_sync", "message", message_id)
        session.commit()
    return RedirectResponse("/logs?tab=recovery&updated=sync", status_code=303)
