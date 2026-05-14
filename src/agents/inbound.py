"""Inbound agent - classifies, scores, drafts reply, and queues for approval."""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel

from ..common.config import settings
from ..db.models import Contact, Conversation, Message
from ..db.session import SessionLocal
from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured
from ..llm.client import LLMClient
from ._notify import notify_approval

logger = logging.getLogger(__name__)

_PERSONAL_DOMAINS = {"gmail.com", "naver.com", "daum.net", "yahoo.com", "hotmail.com"}
_TARGET_COUNTRIES = {"kr", "korea", "jp", "japan", "sg", "th", "vn", "id", "ph", "my"}
_processed: set[str] = set()


class ClassifyResult(BaseModel):
    category: str
    reasoning: str


class ScoreAdjustResult(BaseModel):
    adjustment: int
    reasoning: str


class DraftResult(BaseModel):
    subject: str
    body: str
    language: str
    tone_notes: str = ""


def _normalize_email(email: str) -> str:
    local, _, domain = email.lower().partition("@")
    local = re.sub(r"\+.*$", "", local)
    return f"{local}@{domain}"


def _domain_from_email(email: str) -> str:
    return email.lower().split("@")[-1]


def _base_score(email: str | None, country: str | None) -> int:
    score = 50
    if email:
        dom = _domain_from_email(email)
        if dom in _PERSONAL_DOMAINS:
            score -= 10
        else:
            score += 15
    if country and country.lower() in _TARGET_COUNTRIES:
        score += 15
    return max(0, min(100, score))


class InboundAgent:
    """Handles inbound HubSpot events end-to-end."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        hubspot: HubSpotClient | None = None,
    ) -> None:
        self.llm = llm or LLMClient()
        try:
            self.hubspot = hubspot or HubSpotClient()
        except HubSpotNotConfigured:
            self.hubspot = None

    def handle(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Process an inbound webhook event. Returns summary dict or None if skipped."""
        dedup_key = f"{event.get('object_id')}:{event.get('occurred_at')}"
        if dedup_key in _processed:
            logger.info("Skipping duplicate event: %s", dedup_key)
            return None
        _processed.add(dedup_key)

        contact_info = self._fetch_contact(event)
        classification = self._classify(contact_info)
        score = self._score(contact_info, classification.category)
        channel = self._pick_channel(contact_info)
        draft = self._draft_reply(contact_info, classification, score)
        message_id = self._persist(contact_info, classification, score, channel, draft)

        try:
            notify_approval(
                message_id=message_id,
                subject=draft.subject,
                body_snippet=draft.body,
                score=score,
                category=classification.category,
                channel=channel,
            )
        except Exception:
            logger.warning("Approval notification failed for message %d.", message_id, exc_info=True)

        logger.info(
            "Inbound processed: contact=%s category=%s score=%d msg_id=%d",
            contact_info.get("email", "unknown"),
            classification.category,
            score,
            message_id,
        )

        return {
            "message_id": message_id,
            "category": classification.category,
            "score": score,
            "channel": channel,
        }

    def _fetch_contact(self, event: dict) -> dict[str, Any]:
        return {
            "object_id": event.get("object_id", ""),
            "email": event.get("email", ""),
            "full_name": event.get("full_name", "Unknown"),
            "company": event.get("company", ""),
            "country": event.get("country", ""),
            "lifecycle_stage": event.get("lifecycle_stage", ""),
            "last_message": event.get("last_message", ""),
            "whatsapp_opt_in": event.get("whatsapp_opt_in", False),
            "phone": event.get("phone"),
        }

    def _classify(self, contact_info: dict) -> ClassifyResult:
        return self.llm.complete(
            "inbound/classify",
            {
                "contact_name": contact_info["full_name"],
                "company": contact_info["company"],
                "country": contact_info["country"],
                "lifecycle_stage": contact_info["lifecycle_stage"],
                "last_message": contact_info["last_message"],
            },
            schema=ClassifyResult,
        )

    def _score(self, contact_info: dict, category: str) -> int:
        base = _base_score(contact_info.get("email"), contact_info.get("country"))
        try:
            adj = self.llm.complete(
                "inbound/score_adjust",
                {
                    "contact_name": contact_info["full_name"],
                    "company": contact_info["company"],
                    "country": contact_info["country"],
                    "category": category,
                    "base_score": str(base),
                    "last_message": contact_info["last_message"],
                },
                schema=ScoreAdjustResult,
            )
            return max(0, min(100, base + adj.adjustment))
        except Exception:
            logger.warning("LLM score adjustment failed, using base score.", exc_info=True)
            return base

    def _pick_channel(self, contact_info: dict) -> str:
        if (
            contact_info.get("whatsapp_opt_in")
            and contact_info.get("phone")
            and settings.WHATSAPP_ENABLED
        ):
            return "whatsapp"
        if contact_info.get("email"):
            return "email"
        return "none"

    def _draft_reply(
        self,
        contact_info: dict,
        classification: ClassifyResult,
        score: int,
    ) -> DraftResult:
        return self.llm.complete(
            "inbound/draft_reply",
            {
                "contact_name": contact_info["full_name"],
                "company": contact_info["company"],
                "country": contact_info["country"],
                "category": classification.category,
                "score": str(score),
                "language": "ko" if contact_info.get("country", "").lower() in _TARGET_COUNTRIES else "en",
                "last_message": contact_info["last_message"],
            },
            schema=DraftResult,
        )

    def _persist(
        self,
        contact_info: dict,
        classification: ClassifyResult,
        score: int,
        channel: str,
        draft: DraftResult,
    ) -> int:
        session = SessionLocal()
        try:
            email = contact_info.get("email", "")
            norm = _normalize_email(email) if email else ""

            contact = session.query(Contact).filter_by(normalized_email=norm).first() if norm else None
            if not contact:
                contact = Contact(
                    hubspot_contact_id=contact_info.get("object_id") or None,
                    email=email or None,
                    normalized_email=norm or "unknown",
                    full_name=contact_info["full_name"],
                    company=contact_info.get("company"),
                    domain=_domain_from_email(email) if email else None,
                    country=contact_info.get("country"),
                    lifecycle_stage=contact_info.get("lifecycle_stage"),
                    score=score,
                )
                session.add(contact)
                session.flush()
            else:
                contact.score = score

            conv = (
                session.query(Conversation).filter_by(contact_id=contact.id).first()
            )
            if not conv:
                conv = Conversation(
                    contact_id=contact.id,
                    topic=classification.category,
                    stage="initial",
                )
                session.add(conv)
                session.flush()

            msg = Message(
                conversation_id=conv.id,
                direction="outbound",
                channel=channel,
                from_address=settings.SMTP_FROM_EMAIL or None,
                to_address=email or None,
                subject=draft.subject,
                body=draft.body,
                language=draft.language,
                status="pending_approval",
                score_snapshot=score,
                draft_provider=settings.LLM_PROVIDER,
            )
            session.add(msg)
            session.commit()
            msg_id = msg.id
            return msg_id
        finally:
            session.close()
