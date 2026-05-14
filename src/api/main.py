"""FastAPI entrypoint with routes for agents and n8n integration."""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..agents.approval import ApprovalError, approve, mark_sent, reject
from ..common.config import settings
from ..common.logging import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Sales Automation", version="0.1.0")


# ---------- Middleware ----------


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path in ("/healthz", "/docs", "/openapi.json"):
        return await call_next(request)
    if not settings.INTERNAL_API_TOKEN:
        return JSONResponse(
            status_code=503,
            content={"detail": "INTERNAL_API_TOKEN is not configured; refusing requests."},
        )
    token = request.headers.get("X-Internal-Token", "")
    if token != settings.INTERNAL_API_TOKEN:
        return JSONResponse(status_code=401, content={"detail": "invalid or missing token"})
    return await call_next(request)


# ---------- Health ----------


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


# ---------- Request models ----------


class InboundWebhookBody(BaseModel):
    event_type: str
    object_id: str
    occurred_at: str | None = None


class OutboundRunBody(BaseModel):
    source: str
    filters: dict | None = None


class ApprovalBody(BaseModel):
    approver: str
    action: Literal["approve", "edit", "reject"]
    edited_body: str | None = None
    reason: str | None = None


# ---------- Agent routes ----------


@app.post("/webhook/hubspot/inbound")
def webhook_hubspot_inbound(body: InboundWebhookBody) -> dict:
    from ..agents.inbound import InboundAgent

    agent = InboundAgent()
    agent.handle(body.model_dump())
    return {"status": "accepted"}


@app.post("/run/outbound")
def run_outbound(body: OutboundRunBody) -> dict:
    from ..agents.outbound import OutboundAgent

    agent = OutboundAgent()
    agent.run(source=body.source, filters=body.filters)
    return {"status": "started"}


@app.post("/run/reply_check")
def run_reply_check() -> dict:
    from ..agents.reply_check import run

    stats = run()
    return {"status": "ok", **stats}


@app.post("/run/report")
def run_report(kind: str = "daily") -> dict:
    from ..agents.report import ReportAgent

    agent = ReportAgent()
    result = agent.generate(kind=kind)
    return {"status": "ok", "report": result}


@app.post("/approve/{message_id}")
async def approve_message(message_id: int, body: ApprovalBody) -> dict:
    """Process approve/edit/reject for a pending message, then send and log."""
    logger.info(
        "Approval for message %d: %s by %s",
        message_id,
        body.action,
        body.approver,
    )

    try:
        if body.action in ("approve", "edit"):
            msg = approve(message_id, body.approver, body.edited_body)
        else:
            msg = reject(message_id, body.approver, body.reason)
            return {"status": msg.status, "message_id": msg.id}
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        from ..integrations.senders import send

        await send(msg)
        mark_sent(message_id)
    except Exception:
        logger.exception("Send failed for message %d", message_id)
        raise HTTPException(status_code=500, detail="Send failed")

    try:
        from ..integrations.hubspot import HubSpotClient

        hs = HubSpotClient()
        engagement_id = await hs.create_email_engagement(
            contact_id=str(msg.conversation.contact_id),
            subject=msg.subject or "",
            body=msg.body,
        )
        await hs.close()
        logger.info("Logged HubSpot engagement %s for message %d", engagement_id, message_id)
    except Exception:
        logger.warning("HubSpot engagement logging failed for message %d", message_id, exc_info=True)

    return {"status": "sent", "message_id": msg.id}
