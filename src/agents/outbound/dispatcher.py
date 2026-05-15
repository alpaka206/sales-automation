"""Natural language → outbound source dispatcher (intent router)."""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel

from ...db.models import OutboundIntent
from ...db.session import SessionLocal
from ...llm.client import LLMClient

logger = logging.getLogger(__name__)

KNOWN_SOURCES = ("youtube", "linkedin_comments", "linkedin_csv",
                 "google_search", "job_board", "manual_csv")


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


def dispatch_natural_query(
    llm: LLMClient,
    user_query: str,
) -> dict:
    """Route a natural-language query and either dispatch or park it.

    Returns a dict with keys: status, intent_id (if parked), stats (if dispatched).
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
