"""Inbound agent - classifies, scores, drafts reply, and queues for approval."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from ..common.config import settings
from ..common.domains import is_personal_domain
from ..db.models import Contact, Conversation, Message
from ..db.session import SessionLocal
from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured
from ..llm.client import LLMClient
from ..llm.knowledge import select_relevant_docs
from ._notify import notify_approval
from .inbound_scoring import (  # noqa: F401 — re-exported for callers/tests
    _TARGET_COUNTRIES,
    _base_score,
    _build_enrichment_context,
    _domain_from_email,
    _normalize_email,
)

logger = logging.getLogger(__name__)

# In-memory short-window dedup for webhook retries. Bounded so a long-running
# process can't leak memory; the authoritative dedup is DB-backed
# (_existing_pending_draft_id), so evicting old keys here is harmless.
_processed: set[str] = set()
_PROCESSED_CAP = 10_000


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
        if len(_processed) >= _PROCESSED_CAP:
            _processed.clear()
        _processed.add(dedup_key)

        contact_info = self._fetch_contact(event)

        if not (contact_info.get("last_message") or "").strip():
            logger.info(
                "Inbound skipped — no message body for contact %s (lifecycle=%s). "
                "Will retry once a form/email/note attaches.",
                contact_info.get("object_id", "?"),
                contact_info.get("lifecycle_stage", "?"),
            )
            return {
                "message_id": None,
                "status": "skipped_no_body",
                "object_id": contact_info.get("object_id"),
            }

        # If a draft is already waiting for human action in the same thread, skip
        # the whole pipeline. Stops HubSpot webhook retries / repeated property
        # changes from piling up duplicate drafts before the operator has acted on
        # the first one. After approve/reject, the next webhook will produce a new
        # draft normally.
        existing = self._existing_pending_draft_id(contact_info)
        if existing is not None:
            logger.info(
                "Inbound skipped — pending draft msg %d already awaiting action "
                "in the same thread (contact=%s ticket=%s).",
                existing,
                contact_info.get("object_id", "?"),
                contact_info.get("ticket_id") or "-",
            )
            return {
                "message_id": existing,
                "status": "skipped_existing_pending",
                "object_id": contact_info.get("object_id"),
            }

        classification = self._classify(contact_info)

        if (
            settings.HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS
            and self.hubspot
            and contact_info.get("object_id")
        ):
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
                title="새 인바운드 문의 — 회신 검토 요청",
                inquiry=contact_info.get("last_message"),
                contact_name=contact_info.get("full_name"),
                contact_company=contact_info.get("company"),
                contact_email=contact_info.get("email"),
            )
        except Exception:
            logger.warning("Approval notification failed for message %d.", message_id, exc_info=True)

        self._mirror_to_sheet(contact_info, classification, score, channel, draft, message_id)

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

    def _mirror_to_sheet(
        self,
        contact_info: dict,
        classification: ClassifyResult,
        score: int,
        channel: str,
        draft: DraftResult,
        message_id: int,
    ) -> None:
        """Best-effort append of this inquiry to the Google Sheet mirror."""
        try:
            from ..integrations.google_sheets import record_inbound

            excerpt = (contact_info.get("last_message") or "").strip().replace("\n", " ")
            record_inbound(
                {
                    "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "message_id": message_id,
                    "status": "pending_approval",
                    "category": classification.category,
                    "score": score,
                    "channel": channel,
                    "full_name": contact_info.get("full_name", ""),
                    "email": contact_info.get("email", ""),
                    "company": contact_info.get("company", ""),
                    "country": contact_info.get("country", ""),
                    "subject": draft.subject,
                    "summary": classification.reasoning,
                    "inbound_excerpt": excerpt[:300],
                }
            )
        except Exception:
            logger.debug("Sheet mirror skipped/failed for msg %d.", message_id, exc_info=True)

    def _existing_pending_draft_id(self, contact_info: dict) -> int | None:
        """Return the id of an outbound pending_approval Message in the same thread.

        Thread key: ticket_id if present, otherwise the contact (looked up by
        normalized email, falling back to hubspot_contact_id).
        """
        ticket_id = contact_info.get("ticket_id")
        session = SessionLocal()
        try:
            conv_id: int | None = None
            if ticket_id:
                conv = (
                    session.query(Conversation)
                    .filter_by(hubspot_ticket_id=ticket_id)
                    .first()
                )
                conv_id = conv.id if conv else None
            else:
                email = contact_info.get("email", "")
                norm = _normalize_email(email) if email else ""
                contact = (
                    session.query(Contact).filter_by(normalized_email=norm).first()
                    if norm
                    else None
                )
                if not contact and contact_info.get("object_id"):
                    contact = (
                        session.query(Contact)
                        .filter_by(hubspot_contact_id=str(contact_info["object_id"]))
                        .first()
                    )
                if contact:
                    # Match contact-keyed conv only (mirror the fix in _persist).
                    conv = (
                        session.query(Conversation)
                        .filter_by(contact_id=contact.id, hubspot_ticket_id=None)
                        .first()
                    )
                    conv_id = conv.id if conv else None

            if conv_id is None:
                return None

            existing = (
                session.query(Message)
                .filter_by(
                    conversation_id=conv_id,
                    direction="outbound",
                    status="pending_approval",
                )
                .order_by(Message.created_at.desc())
                .first()
            )
            return existing.id if existing else None
        finally:
            session.close()

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
            "ticket_id": event.get("ticket_id"),
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

        # Ticket events carry the inbound body directly (subject + content). When
        # present, we trust the ticket over the form/email/note fallbacks because
        # that's the explicit source the operator created in HubSpot.
        ticket_id = info.get("ticket_id")
        if ticket_id and self.hubspot and not info["last_message"]:
            try:
                ticket = self.hubspot.get_ticket_sync(ticket_id)
                parts = [p for p in (ticket.subject, ticket.content) if p]
                if parts:
                    info["last_message"] = "\n\n".join(parts)
                    info["inbound_source"] = "ticket"
                    logger.info("Inbound message from ticket %s for contact %s", ticket_id, contact_id)
            except Exception:
                logger.warning("HubSpot ticket fetch failed for %s", ticket_id, exc_info=True)

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

        info["domain_profile"] = None
        email = info.get("email", "")
        if email and settings.INBOUND_DOMAIN_ENRICHMENT_ENABLED:
            dom = _domain_from_email(email)
            if not is_personal_domain(dom):
                try:
                    from .domain_enrichment import analyze_domain

                    profile = analyze_domain(
                        dom, llm=self.llm, hint_company=info.get("company")
                    )
                    if profile is not None:
                        info["domain_profile"] = {
                            "domain": profile.domain,
                            "company_name": profile.company_name,
                            "industry": profile.industry,
                            "services": profile.services,
                            "target_market": profile.target_market,
                            "size_hint": profile.size_hint,
                            "confidence": profile.confidence,
                            "notes": profile.notes,
                        }
                except Exception:
                    logger.warning("Domain enrichment failed for %s", dom, exc_info=True)

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
        base = _base_score(
            contact_info.get("email"),
            contact_info.get("country"),
            contact_info.get("domain_profile"),
        )
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
        knowledge_docs = select_relevant_docs(
            inquiry=contact_info["last_message"],
            category=classification.category,
            scope="inbound",
            llm=self.llm,
        )
        return self.llm.complete(
            "inbound/draft_reply",
            {
                "contact_name": contact_info["full_name"],
                "company": contact_info["company"],
                "country": contact_info["country"],
                "category": classification.category,
                "score": str(score),
                "last_message": contact_info["last_message"],
                "enrichment_context": _build_enrichment_context(contact_info),
                "knowledge_docs": knowledge_docs,
            },
            schema=DraftResult,
            tier="pro",
            max_tokens=4000,
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
                    phone=contact_info.get("phone") or None,
                    whatsapp_opt_in=bool(contact_info.get("whatsapp_opt_in")),
                )
                session.add(contact)
                session.flush()
            else:
                contact.score = score
                if contact_info.get("phone"):
                    contact.phone = contact_info["phone"]
                if contact_info.get("whatsapp_opt_in"):
                    contact.whatsapp_opt_in = True

            # Ticket-based inbound: one ticket = one inquiry = one conversation.
            # The same contact opening multiple tickets gets multiple separate
            # threads (separate draft, separate approval) — that matches the
            # operator's mental model where each ticket is its own work item.
            # Legacy non-ticket inbounds (form/email/note) still collapse to one
            # conversation per contact, since there's no per-inquiry key.
            ticket_id = contact_info.get("ticket_id")
            if ticket_id:
                conv = (
                    session.query(Conversation)
                    .filter_by(hubspot_ticket_id=ticket_id)
                    .first()
                )
                if not conv:
                    conv = Conversation(
                        contact_id=contact.id,
                        topic=classification.category,
                        stage="initial",
                        hubspot_ticket_id=ticket_id,
                    )
                    session.add(conv)
                    session.flush()
            else:
                # Only match contact-keyed conversations (those without a ticket).
                # Otherwise a non-ticket event (e.g. a stale lifecyclestage retry)
                # would land inside a ticket conv that happens to share the contact.
                conv = (
                    session.query(Conversation)
                    .filter_by(contact_id=contact.id, hubspot_ticket_id=None)
                    .first()
                )
                if not conv:
                    conv = Conversation(
                        contact_id=contact.id,
                        topic=classification.category,
                        stage="initial",
                    )
                    session.add(conv)
                    session.flush()

            # Persist the inbound body so the approval UI can show what we're replying to.
            # HubSpot is authoritative but rate-limited and the source row (note/form) can
            # disappear, so we snapshot it locally.
            inbound_body = (contact_info.get("last_message") or "").strip()
            if inbound_body:
                inbound_msg = Message(
                    conversation_id=conv.id,
                    direction="inbound",
                    channel=channel,
                    from_address=email or None,
                    to_address=settings.SMTP_FROM_EMAIL or None,
                    subject=None,
                    body=inbound_body,
                    language=draft.language,
                    status="received",
                )
                session.add(inbound_msg)
                conv.last_incoming_at = datetime.now(timezone.utc)
                session.flush()

            to_addr = contact_info.get("phone") if channel == "whatsapp" else (email or None)
            msg = Message(
                conversation_id=conv.id,
                direction="outbound",
                channel=channel,
                from_address=settings.SMTP_FROM_EMAIL or None,
                to_address=to_addr,
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
