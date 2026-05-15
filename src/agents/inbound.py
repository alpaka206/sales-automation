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
from ..llm.knowledge import load_relevant_docs
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


def _build_enrichment_context(contact_info: dict) -> str:
    """Build optional context block from HubSpot-enriched data."""
    parts: list[str] = []
    if contact_info.get("recent_emails"):
        parts.append(f"Recent email history with this contact:\n{contact_info['recent_emails']}")
    if contact_info.get("deal_summary"):
        parts.append(f"Associated deals:\n{contact_info['deal_summary']}")
    return "\n\n".join(parts)


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

        if self.hubspot and contact_info.get("object_id"):
            try:
                self.hubspot.update_inbound_status_sync(contact_info["object_id"], "analyzed")
            except Exception:
                logger.warning("Failed to set inbound_status=analyzed for %s", contact_info["object_id"], exc_info=True)

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
        info: dict[str, Any] = {
            "object_id": event.get("object_id", ""),
            "email": event.get("email", ""),
            "full_name": event.get("full_name", "Unknown"),
            "company": event.get("company", ""),
            "country": event.get("country", ""),
            "lifecycle_stage": event.get("lifecycle_stage", ""),
            "last_message": event.get("last_message", ""),
            "whatsapp_opt_in": event.get("whatsapp_opt_in", False),
            "phone": event.get("phone"),
            "recent_emails": "",
            "deal_summary": "",
        }

        if not self.hubspot:
            info["inbound_source"] = "event_payload" if info["last_message"] else "none"
            return info

        contact_id = info["object_id"]
        if not contact_id:
            return info

        try:
            hs_contact = self.hubspot.get_contact_sync(contact_id)
            full_name = " ".join(filter(None, [hs_contact.firstname, hs_contact.lastname]))
            if full_name:
                info["full_name"] = full_name
            info["email"] = hs_contact.email or info["email"]
            info["company"] = hs_contact.company or info["company"]
            info["country"] = hs_contact.country or info["country"]
            info["phone"] = hs_contact.phone or info["phone"]
            info["lifecycle_stage"] = hs_contact.lifecyclestage or info["lifecycle_stage"]
        except Exception:
            logger.warning("HubSpot contact fetch failed, using event payload.", exc_info=True)

        # Fetch actual message body: form submission → inbound email → note → event payload
        if not info["last_message"] and self.hubspot:
            inbound_source = None
            body = None
            try:
                body = self.hubspot.get_latest_form_submission(contact_id)
                if body:
                    inbound_source = "form_submission"
            except Exception:
                logger.debug("Form submission fetch failed for %s", contact_id)

            if not body:
                try:
                    body = self.hubspot.get_latest_inbound_email(contact_id)
                    if body:
                        inbound_source = "inbound_email"
                except Exception:
                    logger.debug("Inbound email fetch failed for %s", contact_id)

            if not body:
                try:
                    body = self.hubspot.get_latest_note(contact_id)
                    if body:
                        inbound_source = "note"
                except Exception:
                    logger.debug("Note fetch failed for %s", contact_id)

            if body:
                info["last_message"] = body
                info["inbound_source"] = inbound_source
                logger.info("Inbound message from %s for contact %s", inbound_source, contact_id)
            else:
                info["inbound_source"] = "event_payload"
                if not info["last_message"]:
                    logger.warning("No message body found for contact %s", contact_id)
        elif info["last_message"]:
            info["inbound_source"] = "event_payload"
        else:
            info["inbound_source"] = "none"

        try:
            emails = self.hubspot.get_recent_emails_sync(contact_id, limit=5)
            if emails:
                snippets = []
                for e in emails:
                    subj = e.subject or "(no subject)"
                    body = (e.body or "")[:200]
                    snippets.append(f"- {subj}: {body}")
                info["recent_emails"] = "\n".join(snippets)
        except Exception:
            logger.warning("HubSpot email history fetch failed.", exc_info=True)

        try:
            deals = self.hubspot.get_associated_deals_sync(contact_id)
            if deals:
                parts = []
                for d in deals:
                    parts.append(f"- {d.name or 'Unnamed'} (stage: {d.stage or 'unknown'}, amount: {d.amount or 'N/A'})")
                info["deal_summary"] = "\n".join(parts)
        except Exception:
            logger.warning("HubSpot deals fetch failed.", exc_info=True)

        return info

    def _classify(self, contact_info: dict) -> ClassifyResult:
        return self.llm.complete(
            "inbound/classify",
            {
                "contact_name": contact_info["full_name"],
                "company": contact_info["company"],
                "country": contact_info["country"],
                "lifecycle_stage": contact_info["lifecycle_stage"],
                "last_message": contact_info["last_message"],
                "enrichment_context": _build_enrichment_context(contact_info),
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
                "enrichment_context": _build_enrichment_context(contact_info),
                "knowledge_docs": load_relevant_docs(classification.category),
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
