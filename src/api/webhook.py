"""HubSpot inbound webhook: v3 signature verification, event mapping, and route.

Only ``ticket.creation`` (and the legacy ``contact.creation`` /
``contact.propertyChange:lifecyclestage``) drive inbound. Everything else is
ignored. The route is mounted as a router from ``main.py``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time

from fastapi import APIRouter, HTTPException, Request

from ..common.config import settings
from .schemas import HubSpotWebhookEvent, InboundWebhookBody

logger = logging.getLogger(__name__)

router = APIRouter()

_HUBSPOT_SUBSCRIPTION_MAP: dict[str, str] = {
    "contact.creation": "contact.creation",
    "ticket.creation": "ticket_created",
}

_TICKET_EVENT_TYPES = {"ticket_created"}


def _verify_hubspot_signature(
    request_method: str, request_uri: str, body: bytes, headers: dict[str, str]
) -> None:
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
        # Optional offline-replay dump, gated behind WEBHOOK_DEBUG_DUMP (default off)
        # since this writes the request body+headers to disk on an attacker-
        # triggerable path. Secret material is never written.
        if settings.WEBHOOK_DEBUG_DUMP:
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
    # ticket.propertyChange / hs_pipeline_stage is no longer subscribed — only
    # ticket.creation drives inbound. Any stray ticket.propertyChange falls
    # through to the map below and is ignored.
    return _HUBSPOT_SUBSCRIPTION_MAP.get(sub)


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


@router.post("/webhook/hubspot/inbound")
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

            # agent.handle is sync and calls the Gemini API 3x (classify,
            # score_adjust, draft_reply). On the asyncio loop that would block every
            # other request — including /healthz and /messages — for the duration.
            # to_thread offloads it so the loop stays responsive.
            result = await asyncio.to_thread(agent.handle, internal)
            results.append({"object_id": internal["object_id"], "status": "processed", **(result or {})})
        except Exception:
            logger.exception("Error processing webhook event: %s", item)
            results.append({"object_id": item.get("objectId", item.get("object_id")), "status": "error"})

    return {"status": "accepted", "results": results}
