"""Natural language → outbound source dispatcher (intent router)."""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel

from ...db.models import OutboundIntent
from ...db.session import SessionLocal
from ...llm.client import LLMClient

logger = logging.getLogger(__name__)


class IntentRouterResult(BaseModel):
    """LLM output for intent routing."""

    source: Literal["youtube", "linkedin_comments", "linkedin_csv",
                     "google_search", "job_board", "manual_csv"]
    filters: dict
    confidence: float
    rationale: str
    requires_user_input: list[str] = []


LOW_CONFIDENCE_THRESHOLD = 0.5


def route_intent(llm: LLMClient, user_query: str) -> IntentRouterResult:
    """Call the LLM to route a natural-language query to a source + filters."""
    return llm.complete(
        "outbound/intent_router",
        {"user_query": user_query},
        schema=IntentRouterResult,
    )


def route_and_enqueue(llm: LLMClient, user_query: str) -> dict:
    """Route a natural-language query (light LLM call) and QUEUE it for a local runner.

    The actual crawl/discovery (Playwright + CPU heavy) is NOT run here — it is deferred
    to ``scripts/run_outbound_worker.py`` running on a local machine, because the deployed
    (Render free) instance has no headless browser and too little RAM/CPU to crawl. The web
    only does the cheap Gemini routing call and parks a ``queued`` intent in the DB; the
    local worker polls for it and executes. Low-confidence / needs-more-input intents are
    short-circuited here (no point queuing them).

    Returns a dict with keys: status (rejected|pending_user_input|queued), intent_id, ...
    """
    result = route_intent(llm, user_query)

    if result.confidence < LOW_CONFIDENCE_THRESHOLD:
        intent = _save_intent(user_query, result, status="failed")
        logger.warning(
            "Intent routing confidence too low (%.2f) for query: %s",
            result.confidence, user_query,
        )
        return {
            "status": "rejected",
            "intent_id": intent.id,
            "confidence": result.confidence,
            "rationale": result.rationale,
        }

    if result.requires_user_input:
        intent = _save_intent(user_query, result, status="pending_user_input")
        logger.info(
            "Intent needs user input: %s → %s",
            user_query, result.requires_user_input,
        )
        return {
            "status": "pending_user_input",
            "intent_id": intent.id,
            "requires_user_input": result.requires_user_input,
            "routed_source": result.source,
            "routed_filters": result.filters,
        }

    intent = _save_intent(user_query, result, status="queued")
    logger.info(
        "Intent queued for local worker: #%d %s → %s",
        intent.id, user_query, result.source,
    )
    return {
        "status": "queued",
        "intent_id": intent.id,
        "routed_source": result.source,
        "routed_filters": result.filters,
        "confidence": result.confidence,
    }


def run_queued_intent(llm: LLMClient, intent_id: int) -> dict:
    """Execute a single queued OutboundIntent locally (crawl + score + draft).

    Called by the local outbound worker. Claims the intent (status → ``running``), runs
    the OutboundAgent for the routed source/filters — which dedups already-known emails and
    persists every discovered prospect to the DB — then marks ``dispatched`` or ``failed``.
    """
    with SessionLocal() as session:
        intent = session.get(OutboundIntent, intent_id)
        if not intent:
            return {"status": "missing", "intent_id": intent_id}
        if intent.status not in ("queued", "running"):
            return {"status": "skipped", "intent_id": intent_id, "intent_status": intent.status}
        source = intent.routed_source
        filters = intent.routed_filters or {}
        intent.status = "running"
        session.commit()

    from .agent import OutboundAgent

    try:
        agent = OutboundAgent(llm=llm)
        stats = agent.run(source=source, filters=filters)
        new_status = "dispatched"
    except Exception:
        logger.exception("Local execution of intent #%d failed", intent_id)
        stats = {}
        new_status = "failed"

    with SessionLocal() as session:
        intent = session.get(OutboundIntent, intent_id)
        if intent:
            intent.status = new_status
            session.commit()
    logger.info("Intent #%d %s → %s %s", intent_id, source, new_status, stats)
    return {"status": new_status, "intent_id": intent_id, "source": source, "stats": stats}


def dispatch_natural_query(
    llm: LLMClient,
    user_query: str,
) -> dict:
    """Route AND run a query inline (route + crawl in one call).

    Kept for local/CLI use where the browser is available. The web path uses
    :func:`route_and_enqueue` + the local worker instead, so the deployed instance never
    attempts a crawl. Returns a dict with keys: status, intent_id (if parked), stats.
    """
    result = route_intent(llm, user_query)

    if result.confidence < LOW_CONFIDENCE_THRESHOLD:
        intent = _save_intent(user_query, result, status="failed")
        logger.warning(
            "Intent routing confidence too low (%.2f) for query: %s",
            result.confidence, user_query,
        )
        return {
            "status": "rejected",
            "intent_id": intent.id,
            "confidence": result.confidence,
            "rationale": result.rationale,
        }

    if result.requires_user_input:
        intent = _save_intent(user_query, result, status="pending_user_input")
        logger.info(
            "Intent needs user input: %s → %s",
            user_query, result.requires_user_input,
        )
        return {
            "status": "pending_user_input",
            "intent_id": intent.id,
            "requires_user_input": result.requires_user_input,
            "routed_source": result.source,
            "routed_filters": result.filters,
        }

    from .agent import OutboundAgent

    agent = OutboundAgent(llm=llm)
    stats = agent.run(source=result.source, filters=result.filters)
    _save_intent(user_query, result, status="dispatched")
    logger.info("Intent dispatched: %s → %s %s", user_query, result.source, stats)
    return {"status": "dispatched", "source": result.source, "stats": stats}


def _save_intent(
    user_query: str,
    result: IntentRouterResult,
    status: str,
) -> OutboundIntent:
    """Persist the intent to the database."""
    session = SessionLocal()
    try:
        intent = OutboundIntent(
            user_query=user_query,
            routed_source=result.source,
            routed_filters=result.filters,
            confidence=result.confidence,
            status=status,
        )
        session.add(intent)
        session.commit()
        session.refresh(intent)
        return intent
    finally:
        session.close()
