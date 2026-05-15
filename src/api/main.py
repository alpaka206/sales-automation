"""FastAPI entrypoint with routes for agents and n8n integration."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

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
    skip_paths = ("/healthz", "/docs", "/openapi.json")
    if request.url.path in skip_paths:
        return await call_next(request)

    # HubSpot webhook uses its own signature verification inside the route handler
    if request.url.path == "/webhook/hubspot/inbound" and settings.HUBSPOT_WEBHOOK_SECRET:
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


@app.post("/internal/healthcheck")
def internal_healthcheck() -> dict:
    """Run live connectivity checks and return the report."""
    from ..common.healthcheck import run_healthchecks

    report = run_healthchecks()
    return report.model_dump()


# ---------- Request models ----------


class HubSpotWebhookEvent(BaseModel):
    """Single event from a HubSpot webhook payload."""

    subscriptionType: str
    objectId: int
    occurredAt: int | None = None
    eventId: int | None = None
    propertyName: str | None = None
    propertyValue: str | None = None


class InboundWebhookBody(BaseModel):
    """Legacy internal format — kept for backward compatibility."""

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


_HUBSPOT_SUBSCRIPTION_MAP: dict[str, str] = {
    "contact.creation": "contact.creation",
}

_SIGNATURE_MAX_AGE_SECONDS = 300


def _verify_hubspot_signature(request_method: str, request_uri: str, body: bytes, headers: dict[str, str]) -> None:
    """Verify HubSpot v3 webhook signature. Raises HTTPException(401) on failure."""
    secret = settings.HUBSPOT_WEBHOOK_SECRET
    if not secret:
        return

    signature = headers.get("x-hubspot-signature-v3", "")
    timestamp = headers.get("x-hubspot-request-timestamp", "")

    if not signature or not timestamp:
        raise HTTPException(status_code=401, detail="missing HubSpot signature headers")

    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid timestamp")

    if abs(time.time() * 1000 - ts) > _SIGNATURE_MAX_AGE_SECONDS * 1000:
        raise HTTPException(status_code=401, detail="request timestamp too old")

    # HubSpot v3: HMAC-SHA256(secret, requestMethod + requestUri + requestBody + timestamp)
    message = f"{request_method}{request_uri}{body.decode('utf-8')}{timestamp}"
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid signature")


def _map_hubspot_event(event: HubSpotWebhookEvent) -> str | None:
    """Map HubSpot subscriptionType to internal event_type. Returns None for ignored types."""
    sub = event.subscriptionType
    if sub == "contact.propertyChange" and event.propertyName == "lifecyclestage":
        return "lifecycle_change"
    return _HUBSPOT_SUBSCRIPTION_MAP.get(sub)


@app.post("/webhook/hubspot/inbound")
async def webhook_hubspot_inbound(request: Request) -> dict:
    """Accept HubSpot webhook payload (array or single object) and process each event."""
    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    _verify_hubspot_signature(
        request_method="POST",
        request_uri=str(request.url),
        body=raw_body,
        headers=headers,
    )

    import json

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON")

    # Accept both single object and array
    if isinstance(payload, dict):
        events_raw = [payload]
    elif isinstance(payload, list):
        events_raw = payload
    else:
        raise HTTPException(status_code=400, detail="expected object or array")

    # Detect format: HubSpot native (has subscriptionType) vs legacy internal (has event_type)
    from ..agents.inbound import InboundAgent

    agent = InboundAgent()
    results = []

    for item in events_raw:
        try:
            if "subscriptionType" in item:
                event = HubSpotWebhookEvent(**item)
                event_type = _map_hubspot_event(event)
                if event_type is None:
                    logger.info("Ignoring HubSpot event: %s", event.subscriptionType)
                    results.append({"objectId": event.objectId, "status": "ignored"})
                    continue

                internal = {
                    "event_type": event_type,
                    "object_id": str(event.objectId),
                    "occurred_at": str(event.occurredAt) if event.occurredAt else None,
                }
            else:
                body = InboundWebhookBody(**item)
                internal = body.model_dump()

            result = agent.handle(internal)
            results.append({"object_id": internal["object_id"], "status": "processed", **(result or {})})
        except Exception:
            logger.exception("Error processing webhook event: %s", item)
            results.append({"object_id": item.get("objectId", item.get("object_id")), "status": "error"})

    return {"status": "accepted", "results": results}


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
