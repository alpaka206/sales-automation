"""Inbound agent - classifies, scores, drafts reply, and queues for approval."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from ..common.config import settings
from ..common.domains import is_personal_domain
from ..common.pricing_guard import strip_price_sentences
from ..common.subjects import reply_subject
from ..common.textwash import text_wash
from ..db.conversation_history import add_progress
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

# Fallback acknowledgement body if the editable ``auto_ack`` template is missing.
# ``{name}`` is substituted in code; the whole thing is translated to the inquiry
# language before sending (the language rule is enforced in code, not the prompt).
_DEFAULT_AUTO_ACK_KO = (
    "안녕하세요 {name}님,\n\n"
    "문의 주셔서 감사합니다. 보내주신 메일은 잘 도착했으며, "
    "담당자가 내용을 확인한 뒤 24시간 이내에 답변드리겠습니다.\n\n"
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


class _SummaryResult(BaseModel):
    summary: str = ""
    customer_requests: str = ""


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
            logger.warning(
                "Inbound skipped — empty body (ticket=%s contact=%s email=%s). The ticket has "
                "no subject/content to reply to; add the inquiry text to the ticket.",
                contact_info.get("ticket_id") or "-",
                contact_info.get("object_id", "?"),
                contact_info.get("email") or "?",
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
            contact_info, channel, inquiry_lang
        )

        # Immediate acknowledgement on the FIRST inbound of a thread. Goes out
        # without approval, in the inquiry language, and never changes ticket/draft
        # status. Best-effort: a failure here must not stop the real reply draft.
        if is_first_inbound and channel == "email" and contact_info.get("email"):
            self._maybe_send_auto_ack(contact_info, conv_id, inquiry_lang)

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
            draft = self._draft_reply(contact_info, classification, score, conv_id)
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
            logger.warning(
                "Approval notification failed for message %d.", message_id, exc_info=True
            )

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

            existing = (
                session.query(Message)
                .filter(
                    Message.conversation_id == conv_id,
                    Message.direction == "outbound",
                    Message.status.in_(["pending_approval", "drafting"]),
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
            # ``subject`` = the customer's own subject line (kept separate so the UI
            # shows it AND so the reply subject can be "RE: <subject>"). ``last_message``
            # = the body we reply to (content, falling back to subject if empty).
            "subject": event.get("subject", ""),
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
                if ticket.subject and not info["subject"]:
                    info["subject"] = ticket.subject
                # Body to reply to = ticket content; fall back to the subject so a
                # subject-only ticket ("가끔 제목만 오더라") is never treated as empty.
                body = ticket.content or ticket.subject or ""
                if body:
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

        info["domain_profile"] = None
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
                        Message.direction == "outbound",
                        Message.status == "sent",
                        (Message.prompt_variant.is_(None)) | (Message.prompt_variant != "auto_ack"),
                    )
                    .count()
                )
            return sent == 0
        except Exception:
            logger.warning("first-reply check failed for conv %s; assuming first.", conv_id)
            return True

    def _draft_reply(
        self,
        contact_info: dict,
        classification: ClassifyResult,
        score: int,
        conv_id: int | None = None,
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
                "enrichment_context": _build_enrichment_context(contact_info),
                "knowledge_docs": knowledge_docs,
                "pricing_rule": _PRICING_RULE_FIRST if first_reply else _PRICING_RULE_NORMAL,
            },
            schema=DraftResult,
            tier="pro",
            max_tokens=4000,
        )

        # CODE GUARD 1 — the operator always reviews Korean.
        draft.body = ensure_korean(draft.body, llm=self.llm)
        draft.language = "ko"

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
        self, contact_info: dict, channel: str, inquiry_lang: str
    ) -> tuple[int, int, bool]:
        """Persist the inquiry + a 'drafting' outbound placeholder, before the AI draft.

        Returns ``(outbound_message_id, conversation_id, is_first_inbound)``. The
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
                contact = Contact(
                    hubspot_contact_id=contact_info.get("object_id") or None,
                    email=email or None,
                    normalized_email=norm or "unknown",
                    full_name=contact_info["full_name"],
                    company=contact_info.get("company"),
                    domain=dom,
                    country=contact_info.get("country"),
                    lifecycle_stage=contact_info.get("lifecycle_stage"),
                    phone=contact_info.get("phone") or None,
                    whatsapp_opt_in=bool(contact_info.get("whatsapp_opt_in")),
                )
                session.add(contact)
                session.flush()
            else:
                if contact_info.get("phone"):
                    contact.phone = contact_info["phone"]
                if contact_info.get("whatsapp_opt_in"):
                    contact.whatsapp_opt_in = True

            # Ticket-based inbound: one ticket = one inquiry = one conversation.
            ticket_id = contact_info.get("ticket_id")
            if ticket_id:
                conv = session.query(Conversation).filter_by(hubspot_ticket_id=ticket_id).first()
                if not conv:
                    conv = Conversation(
                        contact_id=contact.id,
                        topic=None,  # set once classified, in _finalize_draft
                        stage="initial",
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
                        topic=None,
                        stage="initial",
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
            if inbound_body:
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

            to_addr = contact_info.get("phone") if channel == "whatsapp" else (email or None)
            msg = Message(
                conversation_id=conv.id,
                direction="outbound",
                channel=channel,
                from_address=settings.SMTP_FROM_EMAIL or None,
                to_address=to_addr,
                subject=None,
                body="",
                status="drafting",
                target_language=inquiry_lang,
                draft_provider=settings.LLM_PROVIDER,
            )
            session.add(msg)
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
    ) -> None:
        """Fill the 'drafting' placeholder with the finished reply → pending_approval."""
        session = SessionLocal()
        try:
            msg = session.get(Message, message_id)
            if not msg:
                return
            msg.subject = draft.subject
            msg.body = draft.body
            msg.language = "ko"  # the draft the operator reviews is always Korean
            msg.target_language = inquiry_lang or msg.target_language
            msg.status = "pending_approval"
            msg.score_snapshot = score
            conv = session.get(Conversation, msg.conversation_id)
            if conv:
                conv.topic = classification.category
                contact = session.get(Contact, conv.contact_id) if conv.contact_id else None
                if contact:
                    contact.score = score
                add_progress(
                    conv.id,
                    "draft",
                    f"AI 회신 초안 작성 완료 (분류: {classification.category}). 검토 대기.",
                    session=session,
                )
            session.commit()
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

        Interim status is ``auto_sending`` — deliberately NOT ``approved`` so the
        background send_worker (which claims ``approved`` rows) can never race this
        inline dispatch and double-send. _dispatch_auto_ack flips it to sent/failed.

        Returns None (skips) if an auto-ack already exists for this conversation, so
        two near-simultaneous first events (webhook + poller) don't double-ack.
        """
        with SessionLocal() as session:
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
                direction="outbound",
                channel="email",
                from_address=settings.SMTP_FROM_EMAIL or None,
                to_address=contact_info.get("email") or None,
                subject=subject,
                body=body,
                language=language,
                target_language=target_language,
                status="auto_sending",
                prompt_variant="auto_ack",
                approved_by="auto_ack",
                approved_at=datetime.now(timezone.utc),
                draft_provider=settings.LLM_PROVIDER,
            )
            session.add(msg)
            session.commit()
            return msg.id

    def _dispatch_auto_ack(self, message_id: int, conv_id: int) -> None:
        """Send the auto-ack inline (no worker, no approval) and record the outcome.

        The send attempt and the terminal status (sent/failed) are committed in the
        SAME session, so a send failure can never strand the row in 'auto_sending'.
        """
        import asyncio

        from ..integrations.senders import send

        sent_ok = False
        try:
            with SessionLocal() as session:
                m = session.get(Message, message_id)
                if m is None:
                    return
                try:
                    asyncio.run(send(m))
                    m.status = "sent"
                    m.sent_at = datetime.now(timezone.utc)
                    sent_ok = True
                except Exception:
                    logger.warning(
                        "Auto-ack send failed for message %d (non-fatal).",
                        message_id,
                        exc_info=True,
                    )
                    m.status = "failed"
                session.commit()
        except Exception:
            logger.warning("Auto-ack dispatch error for message %d.", message_id, exc_info=True)

        add_progress(
            conv_id,
            "auto_ack",
            (
                "자동 접수확인 메일 발송됨 (문의 언어, 승인 없이 즉시)."
                if sent_ok
                else "자동 접수확인 메일 발송 실패 (로그 확인 필요)."
            ),
        )

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
                parts: list[str] = []
                for m in rows:
                    if not (m.body or "").strip():
                        continue
                    who = "고객" if m.direction == "inbound" else "우리"
                    subj = f"[{m.subject}] " if m.subject else ""
                    parts.append(f"{who}: {subj}{m.body.strip()}")
                thread_text = "\n\n".join(parts)[:8000]
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
