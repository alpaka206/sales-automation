"""FastAPI entrypoint — HubSpot webhook, approval, agent run endpoints, web UI."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
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


_API_SKIP_PATHS = ("/healthz", "/docs", "/openapi.json", "/favicon.ico")
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


_FAVICON_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    b'<rect width="32" height="32" rx="6" fill="#10b981"/>'
    b'<text x="16" y="22" font-size="20" text-anchor="middle" fill="white" '
    b'font-family="-apple-system,Segoe UI,sans-serif" font-weight="700">S</text>'
    b"</svg>"
)


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml")


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
    "ticket.creation": "ticket_created",
}

def _verify_hubspot_signature(request_method: str, request_uri: str, body: bytes, headers: dict[str, str]) -> None:
    """Verify HubSpot v3 webhook signature. Raises HTTPException(401) on failure.

    Fail-closed: if HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE is true (default) and either
    the secret is unset or the signature is missing/invalid, the request is rejected.
    """
    secret = settings.HUBSPOT_WEBHOOK_SECRET
    require = settings.HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE

    if not secret:
        logger.warning("webhook reject: HUBSPOT_WEBHOOK_SECRET unset (require=%s)", require)
        if require:
            raise HTTPException(
                status_code=503,
                detail="HUBSPOT_WEBHOOK_SECRET is not configured — refusing unsigned webhook.",
            )
        return

    signature = headers.get("x-hubspot-signature-v3", "")
    timestamp = headers.get("x-hubspot-request-timestamp", "")

    if not signature or not timestamp:
        logger.warning(
            "webhook reject: missing headers (sig_present=%s, ts_present=%s). seen headers=%s",
            bool(signature), bool(timestamp), sorted(headers.keys()),
        )
        raise HTTPException(status_code=401, detail="missing HubSpot signature headers")

    try:
        ts = int(timestamp)
    except ValueError:
        logger.warning("webhook reject: bad timestamp value %r", timestamp)
        raise HTTPException(status_code=401, detail="invalid timestamp")

    age_ms = abs(time.time() * 1000 - ts)
    max_age_ms = settings.HUBSPOT_SIGNATURE_MAX_AGE_SECONDS * 1000
    if age_ms > max_age_ms:
        logger.warning(
            "webhook reject: timestamp too old (age=%.1fs, max=%.1fs). "
            "HubSpot retries (1min, 5min, 30min) will fail unless this max is raised.",
            age_ms / 1000, max_age_ms / 1000,
        )
        raise HTTPException(status_code=401, detail="request timestamp too old")

    # HubSpot v3: HMAC-SHA256(secret, requestMethod + requestUri + requestBody + timestamp)
    # The result is BASE64-encoded (not hex) — that's the spec, and the digest HubSpot
    # sends in X-HubSpot-Signature-v3 is base64.
    message = f"{request_method}{request_uri}{body.decode('utf-8')}{timestamp}"
    expected = base64.b64encode(
        hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()

    if not hmac.compare_digest(expected, signature):
        # Dump everything needed for offline replay. Overwritten on each rejection so
        # data/ doesn't fill up. This lets us re-run HMAC with secret variants.
        try:
            import json as _json
            import os as _os
            _os.makedirs("data", exist_ok=True)
            dump_path = "data/last_rejected_webhook.json"
            with open(dump_path, "w", encoding="utf-8") as f:
                _json.dump(
                    {
                        "method": request_method,
                        "uri": request_uri,
                        "timestamp": timestamp,
                        "body_len": len(body),
                        "body_text": body.decode("utf-8", errors="replace"),
                        "body_b64": base64.b64encode(body).decode(),
                        "received_signature": signature,
                        "expected_signature_with_current_secret": expected,
                        "secret_len": len(secret),
                        "secret_first4": secret[:4],
                        "secret_last4": secret[-4:],
                        "all_headers": dict(headers),
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            logger.warning("webhook reject: dumped to %s for offline replay.", dump_path)
        except Exception:
            logger.exception("webhook reject dump failed.")

        logger.warning(
            "webhook reject: signature mismatch. method=%s uri=%s body_len=%d ts=%s "
            "received_sig_prefix=%s expected_sig_prefix=%s",
            request_method, request_uri, len(body), timestamp,
            signature[:12], expected[:12],
        )
        raise HTTPException(status_code=401, detail="invalid signature")


def _map_hubspot_event(event: HubSpotWebhookEvent) -> str | None:
    """Map HubSpot subscriptionType to internal event_type. Returns None for ignored types.

    Empty propertyValue on a propertyChange is HubSpot's transient state when a
    field is being cleared/reassigned (e.g. lifecyclestage briefly goes empty when
    you "downgrade" from MQL back to Lead). Treat as noise — don't draft a reply
    for a momentarily empty state.
    """
    sub = event.subscriptionType
    if sub == "contact.propertyChange" and event.propertyName == "lifecyclestage":
        if not (event.propertyValue or "").strip():
            return None
        return "lifecycle_change"
    if sub == "ticket.propertyChange" and event.propertyName == "hs_pipeline_stage":
        if not (event.propertyValue or "").strip():
            return None
        return "ticket_stage_change"
    return _HUBSPOT_SUBSCRIPTION_MAP.get(sub)


_TICKET_EVENT_TYPES = {"ticket_created", "ticket_stage_change"}


def _public_request_uri(request: Request, headers: dict[str, str]) -> str:
    """Reconstruct the public URL HubSpot called us on.

    Behind a tunnel/reverse-proxy (cloudflared, ngrok, nginx) `request.url`
    shows the internal address (e.g. http://127.0.0.1:8000/...) but HubSpot
    signs the HMAC over the public URL. We try, in order:
      1. X-Forwarded-Proto + X-Forwarded-Host (set by well-behaved proxies)
      2. X-Forwarded-Proto + Host header (cloudflared quick-tunnels typically
         set Host to the public hostname but skip X-Forwarded-Host)
      3. Just Host header, assuming https (since public tunnels are usually TLS)
      4. request.url as last resort (matches no-proxy case)

    Trusting these headers here is safe: the HMAC verification fails if an
    attacker fakes them, because they don't know the secret.
    """
    forwarded_host = headers.get("x-forwarded-host", "").strip()
    forwarded_proto = headers.get("x-forwarded-proto", "").strip()
    host_header = headers.get("host", "").strip()

    public_host = forwarded_host or host_header
    # If we have a host that isn't our internal bind, assume we're behind a public
    # tunnel and default to https when proto header is missing.
    is_external_host = public_host and public_host not in (
        f"{settings.APP_HOST}:{settings.APP_PORT}",
        f"127.0.0.1:{settings.APP_PORT}",
        f"localhost:{settings.APP_PORT}",
    )
    proto = forwarded_proto or ("https" if is_external_host else request.url.scheme)

    if public_host:
        path_and_query = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return f"{proto}://{public_host}{path_and_query}"
    return str(request.url)


@app.post("/webhook/hubspot/inbound")
async def webhook_hubspot_inbound(request: Request) -> dict:
    """Accept HubSpot webhook payload (array or single object) and process each event."""
    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    public_uri = _public_request_uri(request, headers)
    logger.info(
        "webhook received: computed_uri=%s host=%r xf_host=%r xf_proto=%r body_len=%d",
        public_uri,
        headers.get("host", ""),
        headers.get("x-forwarded-host", ""),
        headers.get("x-forwarded-proto", ""),
        len(raw_body),
    )

    _verify_hubspot_signature(
        request_method="POST",
        request_uri=public_uri,
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

                if event_type in _TICKET_EVENT_TYPES:
                    # Ticket webhooks give us a ticket id; resolve the primary contact
                    # via association so downstream code stays contact-centric.
                    ticket_id = str(event.objectId)
                    from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured
                    try:
                        hs = HubSpotClient()
                        contact_id = await asyncio.to_thread(
                            hs.get_ticket_primary_contact_sync, ticket_id
                        )
                    except HubSpotNotConfigured:
                        contact_id = None
                    if not contact_id:
                        logger.info(
                            "Ticket %s has no associated contact — skipping (subscription=%s).",
                            ticket_id, event.subscriptionType,
                        )
                        results.append({
                            "objectId": event.objectId,
                            "status": "skipped",
                            "reason": "no_contact",
                        })
                        continue

                    internal = {
                        "event_type": event_type,
                        "object_id": contact_id,
                        "ticket_id": ticket_id,
                        "occurred_at": str(event.occurredAt) if event.occurredAt else None,
                    }
                else:
                    internal = {
                        "event_type": event_type,
                        "object_id": str(event.objectId),
                        "occurred_at": str(event.occurredAt) if event.occurredAt else None,
                    }
            else:
                body = InboundWebhookBody(**item)
                internal = body.model_dump()

            # agent.handle is sync and shells out to the claude CLI 3x (classify,
            # score_adjust, draft_reply). On the asyncio loop that would block every
            # other request — including /healthz and /messages — for the duration.
            # to_thread offloads it so the loop stays responsive.
            result = await asyncio.to_thread(agent.handle, internal)
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
