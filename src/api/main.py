"""FastAPI entrypoint with routes for agents and n8n integration."""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
    logger.info("reply_check triggered (placeholder)")
    return {"status": "started"}


@app.post("/run/report")
def run_report(kind: str = "daily") -> dict:
    from ..agents.report import ReportAgent

    agent = ReportAgent()
    result = agent.generate(kind=kind)
    return {"status": "ok", "report": result}


@app.post("/approve/{message_id}")
def approve_message(message_id: int, body: ApprovalBody) -> dict:
    logger.info(
        "Approval for message %d: %s by %s",
        message_id,
        body.action,
        body.approver,
    )
    return {"status": "ok", "message_id": message_id, "action": body.action}
