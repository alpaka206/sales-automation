"""FastAPI entrypoint — HubSpot webhook, approval, agent run endpoints, web UI."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..agents.approval import ApprovalError, approve, mark_sent, reject
from ..common.config import settings
from ..common.logging import setup_logging
from .web.routes import router as web_router

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background workers on startup, signal graceful shutdown on exit.

    Atomic claim in send_worker makes the loop safe under multiple processes,
    but each FastAPI worker still spins its own task — when running under
    `uvicorn --workers N` you'll get N concurrent workers competing for the
    same DB rows. Set DISABLE_BACKGROUND_WORKERS=true on N-1 of them, or run
    a single worker with `gunicorn --workers 1` + separate process for sends.
    """
    tasks: list[asyncio.Task] = []

    if settings.INBOUND_POLL_ENABLED:
        from ..agents.inbound_poller import run_poller

        tasks.append(asyncio.create_task(run_poller(), name="inbound_poller"))
        logger.info("Inbound poller background task started.")

    if settings.SEND_WORKER_ENABLED:
        from ..agents.send_worker import run_send_worker

        tasks.append(asyncio.create_task(run_send_worker(), name="send_worker"))
        logger.info("Send worker background task started.")

    try:
        yield
    finally:
        try:
            from ..agents.send_worker import request_shutdown as _send_shutdown

            _send_shutdown()
        except Exception:
            logger.exception("Send worker shutdown signal failed.")

        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await asyncio.wait_for(t, timeout=10)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        logger.info("Background tasks stopped.")


app = FastAPI(title="Sales Automation", version="0.1.0", lifespan=lifespan)
app.include_router(web_router)


# ---------- Middleware ----------


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


_API_SKIP_PATHS = ("/healthz", "/docs", "/openapi.json")
_WEB_UI_PREFIXES = ("/", "/messages", "/knowledge", "/outbound", "/settings", "/icp-rules", "/prospects", "/unsubscribe")
_LOCALHOST_HOSTS = ("127.0.0.1", "::1", "localhost")


def _is_web_ui_path(path: str) -> bool:
    """Return True for browser-facing web UI routes (not /api, /webhook, etc.)."""
    if path == "/":
        return True
    return any(path.startswith(p) for p in _WEB_UI_PREFIXES if p != "/")


def _trusted_proxies() -> set[str]:
    return {p.strip() for p in (settings.TRUSTED_PROXIES or "").split(",") if p.strip()}


def _client_ip(request: Request) -> str | None:
    """Return the real client IP. X-Forwarded-For is only honored when the immediate
    peer is on TRUSTED_PROXIES — otherwise the header is attacker-controlled."""
    peer = request.client.host if request.client else None
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd and peer and peer in _trusted_proxies():
        return fwd.split(",")[0].strip() or peer
    return peer


def _is_localhost(request: Request) -> bool:
    """Return True when the request originates from localhost.

    Trust model:
      1. If APP_HOST is bound to a loopback address, the OS already guarantees
         no external traffic can reach this process — every request is local.
      2. Otherwise, we inspect the real peer IP. We do NOT honor
         X-Forwarded-For unless the immediate peer is on TRUSTED_PROXIES.
         A naive `X-Forwarded-For` trust would let any external client spoof
         the header and bypass the localhost-only gate.
    """
    if settings.APP_HOST in _LOCALHOST_HOSTS:
        return True
    ip = _client_ip(request)
    return ip in _LOCALHOST_HOSTS


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path in _API_SKIP_PATHS:
        return await call_next(request)

    # HubSpot webhook signature is verified inside the route handler — the middleware
    # must let the request through so the route can run the verifier. The fail-closed
    # behavior (reject unsigned requests) is enforced inside _verify_hubspot_signature.
    if request.url.path == "/webhook/hubspot/inbound":
        return await call_next(request)

    # Web UI routes are allowed from localhost without API token
    if _is_web_ui_path(request.url.path):
        if _is_localhost(request):
            return await call_next(request)
        return JSONResponse(status_code=403, content={"detail": "web UI is localhost-only"})

    if not settings.INTERNAL_API_TOKEN:
        return JSONResponse(
            status_code=503,
            content={"detail": "INTERNAL_API_TOKEN is not configured; refusing requests."},
        )
    token = request.headers.get("X-Internal-Token", "")
    if not hmac.compare_digest(token, settings.INTERNAL_API_TOKEN):
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
    # HMAC token bound to message_id; required unless APPROVAL_REQUIRE_TOKEN=false.
    token: str | None = None


# ---------- Agent routes ----------


_HUBSPOT_SUBSCRIPTION_MAP: dict[str, str] = {
    "contact.creation": "contact.creation",
}

def _verify_hubspot_signature(request_method: str, request_uri: str, body: bytes, headers: dict[str, str]) -> None:
    """Verify HubSpot v3 webhook signature. Raises HTTPException(401) on failure.

    Fail-closed: if HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE is true (default) and either
    the secret is unset or the signature is missing/invalid, the request is rejected.
    """
    secret = settings.HUBSPOT_WEBHOOK_SECRET
    require = settings.HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE

    if not secret:
        if require:
            raise HTTPException(
                status_code=503,
                detail="HUBSPOT_WEBHOOK_SECRET is not configured — refusing unsigned webhook.",
            )
        return

    signature = headers.get("x-hubspot-signature-v3", "")
    timestamp = headers.get("x-hubspot-request-timestamp", "")

    if not signature or not timestamp:
        raise HTTPException(status_code=401, detail="missing HubSpot signature headers")

    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid timestamp")

    max_age_ms = settings.HUBSPOT_SIGNATURE_MAX_AGE_SECONDS * 1000
    if abs(time.time() * 1000 - ts) > max_age_ms:
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
async def approve_message(message_id: int, body: ApprovalBody, request: Request) -> dict:
    """Process approve/edit/reject for a pending message, then send and log.

    Authorization model:
      - The auth middleware already gates this route behind INTERNAL_API_TOKEN
        (X-Internal-Token header) for any non-localhost caller.
      - In addition, when APPROVAL_REQUIRE_TOKEN=true, the body must include a
        per-message HMAC token. This prevents a leaked X-Internal-Token from
        being replayed against arbitrary message IDs (IDOR guard).
    """
    logger.info(
        "Approval for message %d: %s by %s",
        message_id,
        body.action,
        body.approver,
    )

    if settings.APPROVAL_REQUIRE_TOKEN:
        from ..agents.approval import verify_approval_token

        if not verify_approval_token(message_id, body.token or ""):
            raise HTTPException(status_code=403, detail="invalid or missing approval token")

    try:
        if body.action in ("approve", "edit"):
            msg = approve(message_id, body.approver, body.edited_body)
        else:
            msg = reject(message_id, body.approver, body.reason)
            return {"status": msg.status, "message_id": msg.id}
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    from datetime import datetime, timezone as tz

    if msg.scheduled_at is None:
        from ..db.models import Message
        from ..db.session import SessionLocal as _SL

        with _SL() as _sess:
            _m = _sess.get(Message, message_id)
            if _m:
                _m.scheduled_at = datetime.now(tz.utc)
                _sess.commit()

    if settings.SEND_WORKER_ENABLED:
        return {"status": "approved", "message_id": msg.id, "scheduled_at": str(msg.scheduled_at)}

    try:
        from ..integrations.senders import send

        await send(msg)
        mark_sent(message_id)
    except Exception:
        logger.exception("Send failed for message %d", message_id)
        raise HTTPException(status_code=500, detail="Send failed")

    contact_id = str(msg.conversation.contact_id)

    try:
        from ..integrations.hubspot import HubSpotClient

        hs = HubSpotClient()
        engagement_id = await hs.create_email_engagement(
            contact_id=contact_id,
            subject=msg.subject or "",
            body=msg.body,
        )
        logger.info("Logged HubSpot engagement %s for message %d", engagement_id, message_id)
    except Exception:
        logger.warning("HubSpot engagement logging failed for message %d", message_id, exc_info=True)
        hs = None

    try:
        if hs is None:
            from ..integrations.hubspot import HubSpotClient

            hs = HubSpotClient()
        await hs.update_inbound_status(contact_id, "meeting_link_sent")
    except Exception:
        logger.warning("inbound_status update failed for contact %s", contact_id, exc_info=True)
        try:
            from ..db.models import Event
            from ..db.session import SessionLocal

            with SessionLocal() as session:
                session.add(Event(
                    kind="hubspot_status_update_failed",
                    payload={"contact_id": contact_id, "target_status": "meeting_link_sent"},
                ))
                session.commit()
        except Exception:
            logger.exception("Failed to queue status update retry")
    finally:
        if hs:
            await hs.close()

    return {"status": "sent", "message_id": msg.id}
