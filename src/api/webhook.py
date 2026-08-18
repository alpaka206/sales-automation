"""HubSpot ticket webhook verification and durable enqueueing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request

from ..agents.inbound_worker import enqueue_inbound_ticket
from ..common.config import settings
from .schemas import HubSpotWebhookEvent

logger = logging.getLogger(__name__)
router = APIRouter()

_HUBSPOT_SUBSCRIPTION_MAP = {
    "ticket.creation": "ticket_created",
    "ticket.propertyChange": "ticket_stage_changed",
}
MAX_WEBHOOK_BODY_BYTES = 1024 * 1024
MAX_WEBHOOK_EVENTS = 100


def _verify_hubspot_signature(
    request_method: str, request_uri: str, body: bytes, headers: dict[str, str]
) -> None:
    """Verify HubSpot v3 HMAC and reject unsigned requests when configured."""
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
        raise HTTPException(status_code=401, detail="missing HubSpot signature headers")
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid timestamp")
    if abs(time.time() * 1000 - ts) > settings.HUBSPOT_SIGNATURE_MAX_AGE_SECONDS * 1000:
        raise HTTPException(status_code=401, detail="request timestamp too old")

    try:
        decoded_body = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="webhook body must be UTF-8")
    message = f"{request_method}{request_uri}{decoded_body}{timestamp}"
    expected = base64.b64encode(
        hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()
    if hmac.compare_digest(expected, signature):
        return

    if settings.WEBHOOK_DEBUG_DUMP:
        try:
            from pathlib import Path

            path = Path("data/last_rejected_webhook.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "method": request_method,
                        "uri": request_uri,
                        "timestamp": timestamp,
                        "body_len": len(body),
                        "header_names": sorted(headers),
                        "note": "Customer content and credentials intentionally omitted.",
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("webhook reject dump failed")
    raise HTTPException(status_code=401, detail="invalid signature")


def _map_hubspot_event(event: HubSpotWebhookEvent) -> str | None:
    """Which pipeline this event feeds, or None to ignore it.

    Only a move INTO the New stage starts inbound processing (fetch → classify →
    draft). Every other ``hs_pipeline_stage`` change is handled separately by
    :func:`_sync_stage_change`, which just records where the ticket went — it must
    not enqueue a draft for a ticket that moved to Won or Lost.
    """
    event_type = _HUBSPOT_SUBSCRIPTION_MAP.get(event.subscriptionType)
    if event_type != "ticket_stage_changed":
        return event_type
    target = settings.HUBSPOT_TICKET_STAGE_NEW.strip()
    if event.propertyName != "hs_pipeline_stage" or not target:
        return None
    return event_type if event.propertyValue == target else None


# Deletion is deliberately NOT in _HUBSPOT_SUBSCRIPTION_MAP: a mapped type gets enqueued
# as inbound work, and fetching a ticket that no longer exists is not work.
_DELETION_SUBSCRIPTION = "ticket.deletion"


def _handle_deletion(event: HubSpotWebhookEvent) -> int:
    """A ticket deleted in HubSpot takes its thread with it.

    Until now nothing looked for absence: the webhook only ever hears about creations and
    property changes, and the poller sweeps tickets HubSpot has, never the ones it no
    longer has. So a deleted ticket left an unsent draft sitting in 발송 대기 forever,
    waiting on a thread that stopped existing.

    Never raises. A bookkeeping failure must not 500 at HubSpot, which would have it
    redeliver the whole batch.
    """
    if event.subscriptionType != _DELETION_SUBSCRIPTION:
        return 0
    try:
        from ..agents.hubspot_reconcile import delete_by_ticket

        return delete_by_ticket(str(event.objectId))
    except Exception:
        logger.exception("Ticket deletion sync failed for %s", event.objectId)
        return 0


def _sync_stage_change(event: HubSpotWebhookEvent) -> str | None:
    """Record a HubSpot-side stage move on our copy of the conversation.

    Local DB write only, so it is unaffected by the pre-launch external-write guard.
    Never raises: a bookkeeping failure must not make us 500 at HubSpot, which would
    trigger redelivery of the whole batch.
    """
    if (
        _HUBSPOT_SUBSCRIPTION_MAP.get(event.subscriptionType) != "ticket_stage_changed"
        or event.propertyName != "hs_pipeline_stage"
    ):
        return None
    try:
        from ..agents.stage_sync import sync_stage_from_hubspot

        return sync_stage_from_hubspot(
            str(event.objectId), str(event.propertyValue or ""), source="webhook"
        )
    except Exception:
        logger.exception("Stage sync failed for ticket %s", event.objectId)
        return None


def _public_request_uri(request: Request, headers: dict[str, str]) -> str:
    """Reconstruct the public URL used in HubSpot's signature behind a proxy."""
    public_host = headers.get("x-forwarded-host", "").strip() or headers.get(
        "host", ""
    ).strip()
    forwarded_proto = headers.get("x-forwarded-proto", "").strip()
    internal_hosts = {
        f"{settings.APP_HOST}:{settings.APP_PORT}",
        f"127.0.0.1:{settings.APP_PORT}",
        f"localhost:{settings.APP_PORT}",
    }
    proto = forwarded_proto or (
        "https" if public_host and public_host not in internal_hosts else request.url.scheme
    )
    if not public_host:
        return str(request.url)
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{proto}://{public_host}{request.url.path}{query}"


# Canonical path matches the HubSpot Private App's webhook Target URL. The old
# path is kept as a legacy alias so any in-flight delivery during a cutover is not
# lost (the signature is computed over the actual request path, so both verify).
@router.post("/webhooks/hubspot")
@router.post("/webhook/hubspot/inbound")
async def webhook_hubspot_inbound(request: Request) -> dict:
    """Verify and durably enqueue events, then acknowledge without external calls."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_WEBHOOK_BODY_BYTES:
                raise HTTPException(status_code=413, detail="webhook body too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid Content-Length")

    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(status_code=413, detail="webhook body too large")
        raw.extend(chunk)
    raw_body = bytes(raw)
    headers = {key.lower(): value for key, value in request.headers.items()}
    _verify_hubspot_signature(
        request_method="POST",
        request_uri=_public_request_uri(request, headers),
        body=raw_body,
        headers=headers,
    )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON")
    if isinstance(payload, dict):
        events = [payload]
    elif isinstance(payload, list):
        events = payload
    else:
        raise HTTPException(status_code=400, detail="expected object or array")
    if len(events) > MAX_WEBHOOK_EVENTS:
        raise HTTPException(status_code=413, detail="too many webhook events")

    results: list[dict] = []
    for item in events:
        if not isinstance(item, dict) or "subscriptionType" not in item:
            results.append({"objectId": None, "status": "ignored", "reason": "invalid_event"})
            continue
        try:
            event = HubSpotWebhookEvent(**item)
        except (TypeError, ValueError):
            results.append(
                {
                    "objectId": item.get("objectId"),
                    "status": "ignored",
                    "reason": "invalid_event",
                }
            )
            continue
        # 단계 동기화는 **모든** hs_pipeline_stage 이동에 돕니다 — New 로 들어오는 것도
        # 포함해서. 예전에는 아래 분기 안에만 있어서, New 로의 이동은 접수 처리 큐로만 가고
        # 우리 쪽 단계는 접수가 끝날 때까지(실패하면 영영) 안 따라왔습니다.
        synced = _sync_stage_change(event)
        event_type = _map_hubspot_event(event)
        if event_type is None:
            # Not inbound work — but it may be a ticket that is gone, or a stage move we
            # should record.
            if _handle_deletion(event):
                results.append({"objectId": event.objectId, "status": "deleted"})
                continue
            results.append(
                {"objectId": event.objectId, "status": "stage_synced", "stage": synced}
                if synced
                else {"objectId": event.objectId, "status": "ignored"}
            )
            continue

        occurrence_key = None
        if event_type == "ticket_stage_changed":
            occurrence_key = str(event.eventId or event.occurredAt or "stage-new")
        queued = enqueue_inbound_ticket(
            str(event.objectId),
            source="webhook",
            occurred_at=str(event.occurredAt) if event.occurredAt else None,
            hubspot_event_id=str(event.eventId) if event.eventId is not None else None,
            event_type=event_type,
            occurrence_key=occurrence_key,
        )
        results.append(
            {"objectId": event.objectId, "status": "queued" if queued else "duplicate"}
        )
    return {"status": "accepted", "results": results}
