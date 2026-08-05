"""Inbound agent - classifies, scores, drafts reply, and queues for approval."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from ..common.config import settings
from ..common.domains import is_personal_domain
from ..common.pricing_guard import strip_price_sentences
from ..common.subjects import reply_subject
from ..common.textwash import text_wash
from ..db.conversation_history import add_progress
from ..db.models import Contact, Conversation, CustomerProfile, InboundJob, Message
from ..db.session import SessionLocal
from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured
from ..common.sheet_values import (
    COMPANY_TYPES,
    UNKNOWN_COMPANY_TYPE,
    UNKNOWN_COUNTRY,
    country_in_korean,
    normalise_plan,
)
from ..llm.client import LLMClient
from ..llm.knowledge import select_relevant_docs
from ..llm.prompts import apply_link_tokens, get_reply_format
from ._notify import notify_approval_once
from .inbound_scoring import (  # noqa: F401 — re-exported for callers/tests
    _TARGET_COUNTRIES,
    _base_score,
    _build_enrichment_context,
    _domain_from_email,
    _normalize_email,
)

logger = logging.getLogger(__name__)

def _default_signature() -> str | None:
    """Which signature a new draft carries — an operator's choice, read per draft.

    Imported here rather than at module load for the same reason every other template
    helper in this file is: the DB layer must not be pulled in before settings are.
    """
    from ..db.email_templates import default_signature_key

    return default_signature_key()
_DEFAULT_HUBSPOT = object()

# Fallback acknowledgement body if the editable ``auto_ack`` template is missing.
# ``{name}`` is substituted in code; the whole thing is translated to the inquiry
# language before sending (the language rule is enforced in code, not the prompt).
_DEFAULT_AUTO_ACK_KO = (
    "안녕하세요 {name}님,\n\n"
    "문의 주셔서 감사합니다. 보내주신 메일은 잘 도착했으며, "
    "담당자가 내용을 확인한 뒤 곧 자세한 답변을 보내드리겠습니다.\n\n"
    "감사합니다."
)

# Pricing guidance handed to the draft prompt. The FIRST reply must not state any
# amount (a hard rule also enforced by strip_price_sentences); later replies may
# quote real knowledge-base prices.
_PRICING_RULE_FIRST = (
    "이번이 이 고객에게 보내는 **첫 회신**입니다. 금액·가격·요금(숫자)을 절대 적지 "
    "마세요. 대신 '고객 상황에 맞는 스페셜 프로모션을 안내드릴 수 있다'는 정도만 "
    "언급하고, 구체적인 플랜과 금액은 짧은 미팅이나 통화에서 안내하겠다고 자연스럽게 "
    "제안하세요."
)
_PRICING_RULE_NORMAL = (
    "가격·플랜 문의면 고객 사용 사례에 맞는 플랜을 추천하고 지식 베이스에 있는 실제 "
    "금액을 명시하세요(1~3개). 엔터프라이즈 신호(대규모 조직·다수 시트·보안/계약 요건·"
    "대량 사용)가 있을 때만 엔터프라이즈와 영업 미팅을 권하세요. 지식 베이스에 없는 "
    "금액은 절대 만들지 마세요."
)

# Kept for compatibility with older extensions/tests; durable queue keys now
# provide production deduplication and this set is intentionally not consulted.
_processed: set[str] = set()


class ClassifyResult(BaseModel):
    category: str
    reasoning: str


class CompanyTypeResult(BaseModel):
    company_type: str
    confidence: str = ""
    reason: str = ""


class ScoreAdjustResult(BaseModel):
    adjustment: int
    reasoning: str


class DraftResult(BaseModel):
    subject: str
    body: str
    language: str
    tone_notes: str = ""


class _SummaryResult(BaseModel):
    summary: str = ""
    customer_requests: str = ""


class InboundAgent:
    """Handles inbound HubSpot events end-to-end."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        hubspot: HubSpotClient | None | object = _DEFAULT_HUBSPOT,
    ) -> None:
        self.llm = llm or LLMClient()
        if hubspot is not _DEFAULT_HUBSPOT:
            self.hubspot = hubspot
            return
        try:
            self.hubspot = HubSpotClient()
        except HubSpotNotConfigured:
            self.hubspot = None

    def handle(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Process an inbound webhook event. Returns summary dict or None if skipped."""
        contact_info = self._fetch_contact(event)

        expected_stage = settings.HUBSPOT_TICKET_STAGE_NEW.strip()
        if (
            expected_stage
            and contact_info.get("ticket_id")
            and contact_info.get("ticket_stage") != expected_stage
        ):
            logger.info(
                "Inbound skipped — ticket %s is stage %s, expected new stage %s.",
                contact_info.get("ticket_id") or "-",
                contact_info.get("ticket_stage") or "unknown",
                expected_stage,
            )
            return {
                "message_id": None,
                "status": "skipped_not_new",
                "object_id": contact_info.get("object_id"),
            }

        if not (contact_info.get("last_message") or "").strip():
            logger.warning(
                "Inbound skipped — empty body (ticket=%s contact=%s). The ticket has "
                "no subject/content to reply to; add the inquiry text to the ticket.",
                contact_info.get("ticket_id") or "-",
                contact_info.get("object_id", "?"),
            )
            return {
                "message_id": None,
                "status": "skipped_no_body",
                "object_id": contact_info.get("object_id"),
            }

        # Ticket retries must never create a second usable reply. For contact-only
        # events, only an unfinished draft blocks the next genuine customer message.
        resume_message_id = event.get("_draft_message_id")
        existing = self._existing_pending_draft_id(contact_info, resume_message_id)
        if existing is not None:
            logger.warning(
                "Inbound skipped — a draft (msg %d) is already awaiting action in the same "
                "thread (ticket=%s contact=%s). Approve/reject it before a new draft is made.",
                existing,
                contact_info.get("ticket_id") or "-",
                contact_info.get("object_id", "?"),
            )
            return {
                "message_id": existing,
                "status": "skipped_existing_pending",
                "object_id": contact_info.get("object_id"),
            }

        channel = self._pick_channel(contact_info)

        # Detect the inquiry language ONCE, up front. Every reply in this thread —
        # the auto-ack now and the operator's reply later — must go out in this
        # language; that's enforced in code (not the prompt).
        from ..llm.language import detect_language

        inquiry_lang = detect_language(contact_info.get("last_message", ""), llm=self.llm)
        contact_info["inquiry_language"] = inquiry_lang

        # Persist the inquiry + a "drafting" placeholder up front so the ticket shows
        # on the site immediately, before the (slower) AI reply draft is ready. The
        # placeholder flips to pending_approval once the draft finishes.
        message_id, conv_id, is_first_inbound = self._persist_placeholder(
            contact_info,
            channel,
            inquiry_lang,
            resume_message_id=resume_message_id,
            inbound_job_id=event.get("_inbound_job_id"),
        )

        # Immediate acknowledgement on the FIRST inbound of a thread. Goes out
        # without approval, in the inquiry language, and never changes ticket/draft
        # status. It is queued before the external Sheet write so a slow Sheet can
        # never delay the customer receipt email.
        if is_first_inbound and channel == "email" and contact_info.get("email"):
            self._maybe_send_auto_ack(contact_info, conv_id, inquiry_lang)

        # The sales ledger is written as soon as the acknowledgement is queued.
        # AI drafting may take time or fail, but the inbound itself must never wait.
        self._mirror_new_inbound_to_sheet(contact_info, channel, message_id, conv_id)

        # History, deal, and domain research is useful for the detailed draft but
        # must never delay the immediate receipt acknowledgement above.
        self._enrich_draft_context(contact_info)

        classification = None
        score = None
        draft = None
        try:
            classification = self._classify(contact_info)

            if (
                settings.HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS
                and self.hubspot
                and contact_info.get("object_id")
            ):
                try:
                    self.hubspot.update_inbound_status_sync(contact_info["object_id"], "analyzed")
                except Exception:
                    logger.warning(
                        "Failed to set inbound_status=analyzed for %s",
                        contact_info["object_id"],
                        exc_info=True,
                    )

            score = self._score(contact_info, classification.category)
            draft = self._draft_reply(contact_info, classification, score, conv_id, inquiry_lang)
            self._finalize_draft(
                message_id, contact_info, classification, score, draft, conv_id, inquiry_lang
            )
        except Exception:
            # Don't leave the card spinning forever — surface the failure.
            self._mark_draft_failed(message_id)
            raise

        # Refresh the rolling summary + customer requests (best-effort, separate
        # from the append-only progress log). Never breaks the pipeline.
        self._update_summary(conv_id, contact_info)

        # Unconditional: the draft is always pending_approval now, so there is always
        # someone to notify. The old `if auto_approved: dispatch else: notify` fork is
        # gone with the auto-approval branch.
        try:
            notify_approval_once(
                message_id=message_id,
                subject=draft.subject,
                body_snippet=draft.body,
                score=score,
                category=classification.category,
                title="새 인바운드 문의 — 회신 검토 요청",
                inquiry=contact_info.get("last_message"),
                contact_name=contact_info.get("full_name"),
                contact_company=contact_info.get("company"),
                contact_email=contact_info.get("email"),
            )
        except Exception:
            logger.warning(
                "Approval notification failed for message %d.", message_id, exc_info=True
            )

        # A webhook-processed ticket must also be marked for the polling fallback;
        # otherwise the next poll can treat the same HubSpot ticket as unseen.
        if contact_info.get("ticket_id"):
            try:
                from .inbound_poller import _mark_ticket_processed

                _mark_ticket_processed(str(contact_info["ticket_id"]))
            except Exception:
                logger.warning(
                    "Failed to persist ticket dedup marker for %s.",
                    contact_info["ticket_id"],
                    exc_info=True,
                )

        logger.info(
            "Inbound processed: contact_id=%s category=%s score=%d msg_id=%d",
            contact_info.get("object_id", "unknown"),
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

    def _mirror_new_inbound_to_sheet(
        self, contact_info: dict, channel: str, message_id: int, conv_id: int
    ) -> None:
        """Append the new inquiry once and remember its exact external row."""
        try:
            from .sheet_sync import reserve_inbound_client_id
            from ..integrations.google_sheets import record_inbound

            reserved_client_id = reserve_inbound_client_id(conv_id)

            with SessionLocal() as session:
                conv = session.get(Conversation, conv_id)
                if not conv or conv.sheet_inbound_row:
                    return
                contact = session.get(Contact, conv.contact_id)
                profile = session.get(CustomerProfile, conv.contact_id)

            when = datetime.now(timezone.utc)
            raw_when = contact_info.get("occurred_at")
            try:
                if isinstance(raw_when, (int, float)) or str(raw_when or "").isdigit():
                    stamp = float(raw_when)
                    when = datetime.fromtimestamp(stamp / 1000 if stamp > 10_000_000_000 else stamp, timezone.utc)
                elif raw_when:
                    when = datetime.fromisoformat(str(raw_when).replace("Z", "+00:00"))
            except (ValueError, OSError, OverflowError):
                logger.debug("Could not parse inbound occurred_at=%r; using now.", raw_when)

            excerpt = (contact_info.get("last_message") or "").strip().replace("\n", " ")
            qualification = profile.qualification if profile else None
            if not qualification:
                lifecycle = str(contact_info.get("lifecycle_stage") or "").lower()
                qualification = "PQL" if lifecycle in {"customer", "opportunity"} else "MQL"
            result = record_inbound(
                {
                    "client_id": reserved_client_id,
                    "sales_direction": "Inbound",
                    "inquiry_date": when.date().isoformat(),
                    "deal_stage": "New",
                    "deal_stage_detail": "Inquiry",
                    "pipeline": qualification,
                    "company": contact_info.get("company") or "알 수 없음",
                    "full_name": contact_info.get("full_name", ""),
                    "phone": contact_info.get("phone") or "알 수 없음",
                    "email": contact_info.get("email", ""),
                    # HubSpot's IP-derived country, in the language the column is in.
                    "country": country_in_korean(contact_info.get("ip_country"))
                    if contact_info.get("ip_country")
                    else (contact_info.get("country") or UNKNOWN_COUNTRY),
                    "company_type": (profile.industry if profile else None)
                    or self._company_type(contact_info),
                    "channel": "허브스팟" if contact_info.get("object_id") else channel,
                    "plan": normalise_plan(profile.current_plan if profile else None),
                    "user_seq": profile.user_seq if profile else "",
                    "source": profile.source if profile else "",
                    "history": excerpt[:2000],
                    "inquiry_month": when.strftime("%Y-%m"),
                    "inquiry_quarter": f"{when.year}-Q{(when.month - 1) // 3 + 1}",
                }
            )
            if not result:
                return
            with SessionLocal() as session:
                conv = session.get(Conversation, conv_id)
                if not conv:
                    return
                contact = session.get(Contact, conv.contact_id)
                conv.sheet_inbound_row = result.row
                conv.sheet_client_id = result.client_id
                if contact and result.client_id and not contact.sheet_client_id:
                    contact.sheet_client_id = result.client_id
                session.commit()
        except Exception:
            logger.warning("Sheet mirror skipped/failed for msg %d.", message_id, exc_info=True)

    def _existing_pending_draft_id(
        self, contact_info: dict, resume_message_id: int | None = None
    ) -> int | None:
        """Return a reply that means this event should not create another draft.

        Thread key: ticket_id if present, otherwise the contact (looked up by
        normalized email, falling back to hubspot_contact_id). A ticket represents
        one inquiry, so any usable reply blocks retries. Contact-only conversations
        may receive real later replies, so only unfinished drafts block them.
        """
        ticket_id = contact_info.get("ticket_id")
        session = SessionLocal()
        try:
            conv_id: int | None = None
            if ticket_id:
                conv = session.query(Conversation).filter_by(hubspot_ticket_id=ticket_id).first()
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

            status_filter = (
                Message.status.notin_(["draft_failed", "rejected"])
                if ticket_id
                else Message.status.in_(["drafting", "pending_approval", "approved"])
            )
            existing = (
                session.query(Message)
                .filter(
                    Message.conversation_id == conv_id,
                    Message.direction == "outgoing",
                    status_filter,
                    (Message.prompt_variant.is_(None))
                    | (Message.prompt_variant != "auto_ack"),
                )
                .order_by(Message.created_at.desc())
                .first()
            )
            if (
                existing
                and existing.id == resume_message_id
                and existing.status in {"drafting", "draft_failed"}
            ):
                return None
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
            # ``subject`` = the customer's own subject line (kept separate so the UI
            # shows it AND so the reply subject can be "RE: <subject>"). ``last_message``
            # = the body we reply to (content, falling back to subject if empty).
            "subject": event.get("subject", ""),
            "last_message": event.get("last_message", ""),
            "phone": event.get("phone"),
            "ticket_id": event.get("ticket_id"),
            "ticket_stage": event.get("ticket_stage"),
            "recent_emails": "",
            "deal_summary": "",
            "domain_profile": None,
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
        if ticket_id and self.hubspot:
            try:
                ticket = self.hubspot.get_ticket_sync(ticket_id)
                info["ticket_stage"] = ticket.pipeline_stage
                # The reply goes to the address the TICKET is held against, not to
                # whatever the contact record says. A contact can carry an older or
                # personal address; the ticket's is the one the inquiry arrived on, and
                # the operator's rule is that the answer belongs to the ticket.
                ticket_email = ticket.contact_email
                if ticket_email and ticket_email != info.get("email"):
                    logger.info(
                        "Ticket %s: replying to its own address %s (contact record says %s).",
                        ticket_id,
                        ticket_email,
                        info.get("email") or "-",
                    )
                if ticket_email:
                    info["email"] = ticket_email
                if ticket.subject and not info["subject"]:
                    info["subject"] = ticket.subject
                # Body to reply to = ticket content; fall back to the subject so a
                # subject-only ticket ("가끔 제목만 오더라") is never treated as empty.
                body = ticket.content or ticket.subject or ""
                if body and not info["last_message"]:
                    info["last_message"] = body
                    info["inbound_source"] = "ticket"
                    logger.info(
                        "Inbound message from ticket %s for contact %s", ticket_id, contact_id
                    )
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

        return info

    def _enrich_draft_context(self, info: dict[str, Any]) -> None:
        """Add slower CRM/history/domain facts after the receipt email is sent."""
        contact_id = info.get("object_id")
        if self.hubspot and contact_id:
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
                        parts.append(
                            f"- {d.name or 'Unnamed'} (stage: {d.stage or 'unknown'}, amount: {d.amount or 'N/A'})"
                        )
                    info["deal_summary"] = "\n".join(parts)
            except Exception:
                logger.warning("HubSpot deals fetch failed.", exc_info=True)

        email = info.get("email", "")
        if email and settings.INBOUND_DOMAIN_ENRICHMENT_ENABLED:
            dom = _domain_from_email(email)
            if not is_personal_domain(dom):
                try:
                    from .domain_enrichment import analyze_domain

                    profile = analyze_domain(dom, llm=self.llm, hint_company=info.get("company"))
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

    def _company_type(self, contact_info: dict) -> str:
        """기업 종류 for the workbook: what kind of organisation asked.

        HubSpot carries the contact's Company Name but not the category the sales team
        files by, so the name is evidence rather than the answer — and a personal address
        with no company is itself evidence (a 크리에이터, usually). The model picks from a
        closed list; anything off it, or any failure, files as 확인 안 됨 rather than
        inventing a value the column cannot be filtered on.
        """
        domain_profile = contact_info.get("domain_profile") or {}
        try:
            result = self.llm.complete(
                "inbound/company_type",
                {
                    "company": contact_info.get("company") or "",
                    "domain": contact_info.get("domain") or "",
                    "industry_hint": domain_profile.get("services")
                    or domain_profile.get("industry")
                    or "",
                    "inquiry": (contact_info.get("last_message") or "")[:1500],
                },
                schema=CompanyTypeResult,
            )
        except Exception:
            logger.warning("기업 종류 classification failed; filing as 확인 안 됨.", exc_info=True)
            return UNKNOWN_COMPANY_TYPE
        value = (result.company_type or "").strip()
        if value not in COMPANY_TYPES:
            logger.info("기업 종류 %r is not one the column offers; filing as 확인 안 됨.", value)
            return UNKNOWN_COMPANY_TYPE
        return value

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
        # Email is the only reply channel.
        return "email" if contact_info.get("email") else "none"

    def _is_first_reply(self, conv_id: int | None) -> bool:
        """True if no real reply has been SENT in this thread yet (auto-ack excluded).

        Drives the "no pricing in the first email" rule. Pending drafts don't count
        as "already replied" — only an actually-sent operator reply does.
        """
        if not conv_id:
            return True
        try:
            with SessionLocal() as session:
                sent = (
                    session.query(Message)
                    .filter(
                        Message.conversation_id == conv_id,
                        Message.direction == "outgoing",
                        Message.status == "sent",
                        (Message.prompt_variant.is_(None)) | (Message.prompt_variant != "auto_ack"),
                    )
                    .count()
                )
            return sent == 0
        except Exception:
            logger.warning("first-reply check failed for conv %s; assuming first.", conv_id)
            return True

    def _build_conversation_context(
        self,
        conv_id: int | None,
        latest_message: str | None,
        *,
        limit: int = 8,
        max_chars: int = 6000,
    ) -> str:
        """Return the rolling summary and latest completed turns for drafting.

        The current inbound message is already supplied separately to the prompt,
        so its newest matching row is omitted here. Keeping a small, recent window
        prevents long threads from crowding out the customer's latest intent.
        """
        if not conv_id:
            return ""
        try:
            with SessionLocal() as session:
                conv = session.get(Conversation, conv_id)
                rows = (
                    session.query(Message)
                    .filter(
                        Message.conversation_id == conv_id,
                        Message.body != "",
                        Message.status != "drafting",
                    )
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(limit + 1)
                    .all()
                )

            latest = text_wash(latest_message)
            skipped_latest = False
            prior_rows: list[Message] = []
            for row in rows:
                body = text_wash(row.body)
                if (
                    not skipped_latest
                    and latest
                    and row.direction == "inbound"
                    and body == latest
                ):
                    skipped_latest = True
                    continue
                prior_rows.append(row)
                if len(prior_rows) >= limit:
                    break

            parts: list[str] = []
            if conv and (conv.summary or "").strip():
                parts.append(f"기존 대화 요약:\n{conv.summary.strip()}")
            if conv and (conv.customer_requests or "").strip():
                parts.append(f"기존 고객 요청사항:\n{conv.customer_requests.strip()}")
            if prior_rows:
                turns: list[str] = []
                for row in reversed(prior_rows):
                    label = "고객" if row.direction == "inbound" else "우리"
                    body = text_wash(row.body)[:1200]
                    subject = f" [{row.subject}]" if row.subject else ""
                    turns.append(f"{label}{subject}: {body}")
                parts.append("최근 대화:\n" + "\n\n".join(turns))
            return "\n\n".join(parts)[:max_chars]
        except Exception:
            logger.warning("Conversation context lookup failed for conv %s.", conv_id, exc_info=True)
            return ""

    def _draft_reply(
        self,
        contact_info: dict,
        classification: ClassifyResult,
        score: int,
        conv_id: int | None = None,
        inquiry_lang: str | None = None,
    ) -> DraftResult:
        """Draft the Korean working reply the operator reviews.

        Hard rules enforced in CODE here (not left to the model):
        - the draft is always Korean (``ensure_korean``);
        - the first reply contains no prices (``strip_price_sentences``);
        - the subject is "RE: <customer subject>" with no duplicate prefixes, in
          the inquiry's language (``reply_subject``).
        """
        from ..llm.reply import ensure_korean

        knowledge_docs = select_relevant_docs(
            inquiry=contact_info["last_message"],
            category=classification.category,
            scope="inbound",
            llm=self.llm,
            # 한국어 문의에는 KR 문서, 그 외에는 ENG 문서. 기본 메일 템플릿이 두 벌이라
            # 언어를 안 가리면 두 벌이 같이 붙습니다.
            language=inquiry_lang,
        )
        first_reply = self._is_first_reply(conv_id)
        draft = self.llm.complete(
            "inbound/draft_reply",
            {
                "contact_name": contact_info["full_name"],
                "company": contact_info["company"],
                "country": contact_info["country"],
                "category": classification.category,
                "score": str(score),
                "last_message": contact_info["last_message"],
                "conversation_context": self._build_conversation_context(
                    conv_id, contact_info["last_message"]
                ),
                "enrichment_context": _build_enrichment_context(contact_info),
                "knowledge_docs": knowledge_docs,
                "pricing_rule": _PRICING_RULE_FIRST if first_reply else _PRICING_RULE_NORMAL,
                # The reply SHAPE (opening / middle / closing), edited in the console.
                # Read per draft, so a change there lands on the next reply.
                "reply_format": get_reply_format(),
            },
            schema=DraftResult,
            tier="pro",
            max_tokens=4000,
        )

        # CODE GUARD 1 — the operator always reviews Korean.
        draft.body = ensure_korean(draft.body, llm=self.llm)
        draft.language = "ko"

        # CODE GUARD 1b — links are substituted, never generated. Runs AFTER
        # ensure_korean: translation would happily rewrite a URL.
        draft.body = apply_link_tokens(draft.body)

        # CODE GUARD 2 — the first reply must never state a price. Strip offending
        # lines deterministically and record it on the progress log.
        if first_reply and draft.body:
            cleaned, removed = strip_price_sentences(draft.body)
            if removed:
                draft.body = cleaned
                logger.warning(
                    "First-reply pricing guard removed %d line(s) (contact=%s): %s",
                    len(removed),
                    contact_info.get("email") or "?",
                    " | ".join(removed)[:300],
                )
                if conv_id:
                    add_progress(
                        conv_id,
                        "guard",
                        f"첫 회신 금액 표기 {len(removed)}건 자동 제거됨 (규칙: 첫 메일 금액 금지).",
                    )

        # CODE GUARD 3 — subject is "RE: <customer subject>" (or a localized generic
        # when the inbound had none), with no stacked RE:. Never the raw model subject.
        draft.subject = reply_subject(
            contact_info.get("subject"), target_code=contact_info.get("inquiry_language")
        )
        return draft

    def _persist_placeholder(
        self,
        contact_info: dict,
        channel: str,
        inquiry_lang: str,
        *,
        resume_message_id: int | None = None,
        inbound_job_id: int | None = None,
    ) -> tuple[int, int, bool]:
        """Persist the inquiry and a drafting reply placeholder before the AI draft.

        Returns ``(reply_message_id, conversation_id, is_first_inbound)``. The
        card appears on the site immediately as "작성중"; _finalize_draft fills it in
        once the reply is ready. ``is_first_inbound`` is True when this is the very
        first inbound message in the thread (drives the immediate auto-ack).
        """
        session = SessionLocal()
        try:
            email = contact_info.get("email", "")
            norm = _normalize_email(email) if email else ""

            contact = (
                session.query(Contact).filter_by(normalized_email=norm).first() if norm else None
            )
            if not contact:
                # Only store a domain for REAL company domains. Personal/free-email
                # senders (gmail, naver, …) must not be grouped together as one
                # "company" — that would leak one customer's history to another.
                dom = _domain_from_email(email) if email else None
                if dom and is_personal_domain(dom):
                    dom = None
                anonymous_key = (
                    contact_info.get("object_id")
                    or contact_info.get("ticket_id")
                    or hashlib.sha256(
                        f"{contact_info.get('full_name')}|{contact_info.get('last_message')}".encode()
                    ).hexdigest()[:20]
                )
                contact = Contact(
                    hubspot_contact_id=contact_info.get("object_id") or None,
                    email=email or None,
                    normalized_email=norm or f"unknown:{anonymous_key}",
                    full_name=contact_info["full_name"],
                    company=contact_info.get("company"),
                    domain=dom,
                    country=contact_info.get("country"),
                    lifecycle_stage=contact_info.get("lifecycle_stage"),
                    phone=contact_info.get("phone") or None,
                )
                session.add(contact)
                session.flush()
            else:
                contact.hubspot_contact_id = (
                    contact.hubspot_contact_id or contact_info.get("object_id") or None
                )
                contact.email = email or contact.email
                contact.full_name = contact_info.get("full_name") or contact.full_name
                contact.company = contact_info.get("company") or contact.company
                contact.country = contact_info.get("country") or contact.country
                contact.lifecycle_stage = (
                    contact_info.get("lifecycle_stage") or contact.lifecycle_stage
                )
                if not contact.domain and email:
                    dom = _domain_from_email(email)
                    contact.domain = None if is_personal_domain(dom) else dom
                if contact_info.get("phone"):
                    contact.phone = contact_info["phone"]

            profile = session.get(CustomerProfile, contact.id)
            if profile is None:
                session.add(
                    CustomerProfile(
                        contact_id=contact.id,
                        customer_state="negotiation",
                        pipeline_stage="new",
                        source="hubspot" if contact_info.get("object_id") else "local",
                    )
                )

            # Ticket-based inbound: one ticket = one inquiry = one conversation.
            ticket_id = contact_info.get("ticket_id")
            if ticket_id:
                conv = session.query(Conversation).filter_by(hubspot_ticket_id=ticket_id).first()
                if not conv:
                    conv = Conversation(
                        contact_id=contact.id,
                        # The customer's own subject line, captured at ingest. It used to
                        # be left None here and filled with the AI category later.
                        inquiry_subject=(contact_info.get("subject") or "").strip() or None,
                        stage="new",
                        hubspot_ticket_id=ticket_id,
                        inquiry_language=inquiry_lang,
                    )
                    session.add(conv)
                    session.flush()
            else:
                conv = (
                    session.query(Conversation)
                    .filter_by(contact_id=contact.id, hubspot_ticket_id=None)
                    .first()
                )
                if not conv:
                    conv = Conversation(
                        contact_id=contact.id,
                        inquiry_subject=(contact_info.get("subject") or "").strip() or None,
                        stage="new",
                        inquiry_language=inquiry_lang,
                    )
                    session.add(conv)
                    session.flush()

            # The thread language is set from the first inbound and kept stable.
            if not conv.inquiry_language and inquiry_lang:
                conv.inquiry_language = inquiry_lang

            # First inbound in the thread? (count BEFORE inserting this one.)
            prior_inbound = (
                session.query(Message)
                .filter(Message.conversation_id == conv.id, Message.direction == "inbound")
                .count()
            )
            is_first_inbound = prior_inbound == 0

            # Snapshot the inbound body + subject so the approval UI shows what we're
            # replying to (subject kept separate — fixes "가끔 제목만 오더라").
            inbound_body = (contact_info.get("last_message") or "").strip()
            inbound_subject = (contact_info.get("subject") or "").strip() or None
            # A durable retry of the same HubSpot ticket may replace a failed
            # draft, but it must not append the customer's inquiry a second time.
            if inbound_body and (not ticket_id or is_first_inbound):
                # If this is a later customer message, the latest detailed reply was
                # answered. Auto acknowledgements do not count as sales replies.
                # "test_sent" is the safe-mode counterpart of "sent" (the mail was
                # dispatched, just force-routed to the pre-launch test address). Matching
                # only "sent" left Message.replied permanently False before go-live,
                # which zeroed the reply-rate report and the /messages?status=replied view.
                latest_reply = (
                    session.query(Message)
                    .filter(
                        Message.conversation_id == conv.id,
                        Message.direction == "outgoing",
                        Message.status.in_(("sent", "test_sent")),
                        (Message.prompt_variant.is_(None))
                        | (Message.prompt_variant != "auto_ack"),
                    )
                    .order_by(Message.sent_at.desc(), Message.id.desc())
                    .first()
                )
                if latest_reply:
                    latest_reply.replied = True
                session.add(
                    Message(
                        conversation_id=conv.id,
                        direction="inbound",
                        channel=channel,
                        from_address=email or None,
                        to_address=settings.SMTP_FROM_EMAIL or None,
                        subject=inbound_subject,
                        body=inbound_body,
                        language=inquiry_lang or "en",
                        status="received",
                    )
                )
                conv.last_incoming_at = datetime.now(timezone.utc)
                session.flush()
                # Append-only progress entry: inquiry received.
                excerpt = (inbound_subject or inbound_body).replace("\n", " ").strip()
                add_progress(
                    conv.id,
                    "inbound",
                    f"고객 문의 접수: {excerpt[:140]}",
                    session=session,
                )

            to_addr = email or None
            msg = session.get(Message, resume_message_id) if resume_message_id else None
            if not (
                msg
                and msg.conversation_id == conv.id
                and msg.direction == "outgoing"
                and msg.prompt_variant != "auto_ack"
                and msg.status in {"drafting", "draft_failed"}
            ):
                msg = Message(
                    conversation_id=conv.id,
                    direction="outgoing",
                    channel=channel,
                    from_address=settings.SMTP_FROM_EMAIL or None,
                    to_address=to_addr,
                    subject=None,
                    body="",
                    status="drafting",
                    signature_key=_default_signature(),
                    target_language=inquiry_lang,
                    draft_provider=settings.LLM_PROVIDER,
                )
                session.add(msg)
                session.flush()
            else:
                # Resume the exact placeholder linked to this durable job.  A
                # separate ticket-change job has no such link and remains blocked.
                msg.status = "drafting"
                msg.subject = None
                msg.body = ""

                # If the process died before creating the first auto-ack, retry it.
                has_auto_ack = (
                    session.query(Message.id)
                    .filter(
                        Message.conversation_id == conv.id,
                        Message.prompt_variant == "auto_ack",
                    )
                    .first()
                    is not None
                )
                is_first_inbound = prior_inbound == 1 and not has_auto_ack

            if inbound_job_id:
                job = session.get(InboundJob, inbound_job_id)
                if job and job.status == "processing":
                    payload = dict(job.payload or {})
                    payload["draft_message_id"] = msg.id
                    job.payload = payload
            session.commit()
            return msg.id, conv.id, is_first_inbound
        finally:
            session.close()

    def _finalize_draft(
        self,
        message_id: int,
        contact_info: dict,
        classification: ClassifyResult,
        score: int,
        draft: DraftResult,
        conv_id: int | None = None,
        inquiry_lang: str | None = None,
    ) -> bool:
        """Finalize the draft and return whether score-based auto-approval applied."""
        session = SessionLocal()
        try:
            msg = session.get(Message, message_id)
            if not msg:
                return False
            msg.subject = draft.subject
            msg.body = draft.body
            msg.language = "ko"  # the draft the operator reviews is always Korean
            msg.target_language = inquiry_lang or msg.target_language
            # A detailed reply ALWAYS waits for a human. This used to be a score-vs-
            # AUTO_SEND_THRESHOLD comparison that could set "approved" on its own; the
            # operator's rule is that only the receipt acknowledgement ever sends by
            # itself, so the branch is gone rather than merely configured shut. Nothing
            # in config can reopen it — see tests/test_safe_mode.py.
            msg.status = "pending_approval"
            msg.score_snapshot = score
            conv = session.get(Conversation, msg.conversation_id)
            if conv:
                # 목록이 보여줄 값. 유형은 스레드의 성질이라 대화에 답니다 — 그리고 이것이
                # "검토 필요" 문구를 대신합니다: CS 문의인지 스팸인지가 "확인이 필요합니다"
                # 보다 무엇을 먼저 열어야 하는지를 정확히 말해 줍니다.
                conv.inquiry_category = classification.category
                contact = session.get(Contact, conv.contact_id) if conv.contact_id else None
                if contact:
                    contact.score = score
                # No progress entry: "초안 작성 완료. 검토 대기." is exactly what the
                # pending_approval status already says, on the same screen.
            session.commit()
            return False
        finally:
            session.close()

    # ----- Immediate auto-acknowledgement (first inbound only) -----

    def _maybe_send_auto_ack(self, contact_info: dict, conv_id: int, inquiry_lang: str) -> None:
        """Send the immediate "received, will reply in 24h" acknowledgement.

        Sent WITHOUT approval and in the inquiry's language (enforced in code:
        Korean template → translate_to). Recorded in the thread as an auto_ack
        message but never changes the ticket/draft status. Best-effort throughout.
        """
        if not settings.INBOUND_AUTO_ACK_ENABLED:
            return
        try:
            from ..db.email_templates import get_email_template
            from ..llm.translate import translate_to

            name = (contact_info.get("full_name") or "").strip() or "고객님"
            template = get_email_template("auto_ack", language="ko") or _DEFAULT_AUTO_ACK_KO
            ko_body = template.replace("{name}", name)

            lang = (inquiry_lang or "ko").lower()
            if lang != "ko":
                translated = translate_to(ko_body, lang, llm=self.llm)
                body = text_wash(translated) if translated else text_wash(ko_body)
                # If translation failed we keep Korean rather than dropping the ack.
                final_lang = lang if translated else "ko"
            else:
                body = text_wash(ko_body)
                final_lang = "ko"

            subject = reply_subject(contact_info.get("subject"), target_code=lang)
            msg_id = self._persist_auto_ack(conv_id, contact_info, subject, body, final_lang, lang)
            if msg_id is not None:
                self._dispatch_auto_ack(msg_id, conv_id)
        except Exception:
            logger.warning("Auto-ack failed for conv %s (non-fatal).", conv_id, exc_info=True)

    def _persist_auto_ack(
        self,
        conv_id: int,
        contact_info: dict,
        subject: str,
        body: str,
        language: str,
        target_language: str,
    ) -> int | None:
        """Insert the auto-ack message in an interim state, returning its id.

        Status is ``approved`` so both the immediate attempt and transient retries use
        the same atomic claim and lease as every other outbound message.

        Returns None (skips) if an auto-ack already exists for this conversation, so
        two near-simultaneous first events (webhook + poller) don't double-ack.
        """
        with SessionLocal() as session:
            # Serialise the check+insert on the parent row. PostgreSQL honours the
            # row lock; SQLite serialises the write transaction itself.
            session.query(Conversation).filter(Conversation.id == conv_id).with_for_update().one()
            existing = (
                session.query(Message)
                .filter(
                    Message.conversation_id == conv_id,
                    Message.prompt_variant == "auto_ack",
                )
                .first()
            )
            if existing is not None:
                return None
            msg = Message(
                conversation_id=conv_id,
                direction="outgoing",
                channel="email",
                from_address=settings.SMTP_FROM_EMAIL or None,
                to_address=contact_info.get("email") or None,
                subject=subject,
                body=body,
                language=language,
                target_language=target_language,
                status="approved",
                prompt_variant="auto_ack",
                signature_key=_default_signature(),
                approved_by="auto_ack",
                approved_at=datetime.now(timezone.utc),
                draft_provider=settings.LLM_PROVIDER,
            )
            session.add(msg)
            try:
                session.commit()
            except IntegrityError:
                # A concurrent webhook/poller transaction won the unique
                # one-auto-ack-per-conversation constraint.
                session.rollback()
                return None
            return msg.id

    def _dispatch_auto_ack(self, message_id: int, conv_id: int) -> None:
        """Attempt immediately; known transient SMTP failures stay in the send queue."""
        import asyncio

        from .send_worker import send_approved_now

        try:
            sent_ok = asyncio.run(send_approved_now(message_id))
        except Exception:
            logger.warning("Auto-ack dispatch error for message %d.", message_id, exc_info=True)
            sent_ok = False

        # Only the failure. A successful acknowledgement IS the first outgoing message
        # in the thread above — a log line repeating it is a second copy of the same
        # fact. A failure is a different sentence and needs a person, so it keeps its
        # own kind and stays on 처리 경과.
        if not sent_ok:
            add_progress(
                conv_id,
                "auto_ack_failed",
                "자동 접수확인 메일 발송 재시도 대기 또는 수동 확인 필요.",
            )

    # _dispatch_approved() lived here. Its only caller was the auto-approval branch,
    # which is gone: a detailed reply is never approved without a human, so there is
    # never a freshly-approved draft for the inbound agent to send inline.

    # ----- Rolling summary + customer requests -----

    def _update_summary(self, conv_id: int | None, contact_info: dict) -> None:
        """Refresh the conversation's rolling summary + customer_requests (best-effort)."""
        if not conv_id:
            return
        try:
            with SessionLocal() as session:
                rows = (
                    session.query(Message)
                    .filter(Message.conversation_id == conv_id)
                    .order_by(Message.created_at.asc(), Message.id.asc())
                    .all()
                )
                # Keep the newest complete turns when a thread is long. Taking
                # the first N characters would discard the very message that
                # triggered the current reply.
                newest_first: list[str] = []
                used = 0
                for m in reversed(rows):
                    if not (m.body or "").strip():
                        continue
                    who = "고객" if m.direction == "inbound" else "우리"
                    subj = f"[{m.subject}] " if m.subject else ""
                    part = f"{who}: {subj}{m.body.strip()}"
                    remaining = 8000 - used
                    if remaining <= 0:
                        break
                    if len(part) > remaining:
                        if newest_first:
                            break
                        part = part[-remaining:]
                    newest_first.append(part)
                    used += len(part) + 2
                thread_text = "\n\n".join(reversed(newest_first))
            if not thread_text:
                return

            result = self.llm.complete(
                "inbound/summarize_thread",
                {
                    "contact_name": contact_info.get("full_name", ""),
                    "company": contact_info.get("company", ""),
                    "thread_text": thread_text,
                },
                schema=_SummaryResult,
                tier="flash",
                max_tokens=1200,
            )
            with SessionLocal() as session:
                conv = session.get(Conversation, conv_id)
                if conv:
                    conv.summary = (result.summary or "").strip() or conv.summary
                    conv.customer_requests = (
                        result.customer_requests or ""
                    ).strip() or conv.customer_requests
                    session.commit()
        except Exception:
            logger.warning(
                "Summary refresh failed for conv %s (non-fatal).", conv_id, exc_info=True
            )

    def _mark_draft_failed(self, message_id: int) -> None:
        """Flip a stuck 'drafting' placeholder to 'draft_failed' so it doesn't spin."""
        try:
            with SessionLocal() as session:
                msg = session.get(Message, message_id)
                if msg and msg.status == "drafting":
                    msg.status = "draft_failed"
                    session.commit()
        except Exception:
            logger.warning("Could not mark draft %s failed", message_id, exc_info=True)
