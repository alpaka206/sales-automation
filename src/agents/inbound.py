"""Inbound agent - classifies, scores, drafts reply, and queues for approval."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

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
from ..llm.prompts import apply_editable_tokens, canonicalize_contact_links, get_reply_format
from ._notify import notify_approval_once
from .inbound_scoring import (  # noqa: F401 — re-exported for callers/tests
    _TARGET_COUNTRIES,
    _base_score,
    _build_enrichment_context,
    _domain_from_email,
    _normalize_email,
)
from .summaries import append_summary_line
from .stage_sync import _retire_superseded_drafts

logger = logging.getLogger(__name__)


def _default_signature() -> str | None:
    """어느 서명으로 시작할지 — 목록의 첫 서명. 그 건에서 바꾸는 것은 검토 화면입니다.

    Imported here rather than at module load for the same reason every other template
    helper in this file is: the DB layer must not be pulled in before settings are.
    """
    from ..db.email_templates import default_signature_key

    return default_signature_key()


_DEFAULT_HUBSPOT = object()

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
    "가격·플랜 문의면 고객 사용 사례에 맞는 플랜을 **추천**하되, 단가나 구체적인 금액 "
    "숫자는 본문에 쓰지 마세요. 참고 문서에 금액표가 있어도 그 숫자는 어느 플랜을 권할지 "
    "판단하는 용도이지 옮겨 적는 값이 아닙니다. 금액은 미팅·채팅에서 개별 안내하겠다고 "
    "쓰세요."
)

_HANGUL_RE = re.compile(r"[가-힣]")


def _subject_in_inquiry_language(subject: str | None, inquiry_language: str | None) -> str:
    """정책 문서가 들고 온 메일 제목을 **문의 언어로** 맞춥니다.

    제목이 오는 길은 둘입니다. ``reply_subject`` 가 만드는 「RE: <고객이 쓴 제목>」 은 고객의
    말을 그대로 쓰므로 언제나 고객의 언어이고, 손대면 안 됩니다 — 번역하면 메일 클라이언트가
    제목으로 잇던 스레드가 끊깁니다. 나머지 하나가 여기서 다루는 것: 정책 문서에 적힌 고정
    제목은 **운영자가 쓴 우리 문장**이라 문의 언어와 무관하게 그 문서의 언어로 나갑니다.

    실제로 한국어 문의에 한국어 본문 + 영어 제목이 나갔습니다 —
    ``[Perso Dubbing] Next steps on your customizable plan`` (2026-08-26, msg 62).

    **번역 단계가 아니라 여기서 맞춥니다.** 초안 본문은 검토를 위해 늘 한국어로 쓰였다가
    승인 때 문의 언어로 번역되는데, 제목까지 그 길을 태우면 운영자가 영어로 써 둔 제목이
    한국어를 거쳐 영어로 되돌아옵니다 — 왕복하면서 문장이 상합니다. 제목은 처음부터 문의
    언어로 만들어 두고 번역 단계는 건드리지 않습니다(``messages.message_translate`` 의
    「제목은 이미 맞는 언어다」라는 전제가 그래야 참이 됩니다).

    번역이 실패하면 원문을 그대로 씁니다. 제목 없는 메일보다는 낫습니다.
    """
    from ..llm.translate import translate_to

    subject = (subject or "").strip()
    if not subject:
        return ""
    target = (inquiry_language or "en").strip().lower()[:2] or "en"
    # **글자가 섞여 있는지가 아니라 한글이 하나라도 있는지로 봅니다.** ``is_mostly_korean``
    # 은 글자 수 비율이라 제목에는 못 씁니다 — 이 제목들은 하나같이 ``[Perso Dubbing]`` 으로
    # 시작하고, 그 브랜드 이름의 로마자 열두 자가 뒤따르는 한글 아홉 자를 이겨서 멀쩡한 국문
    # 제목이 「한국어가 아님」으로 나옵니다. 영문 제목에 한글이 섞일 일은 없으므로 존재
    # 여부면 충분합니다.
    #
    # 정책 문서 제목은 국문 아니면 영문이라 그 둘은 모델을 부르지 않고 넘기고, 제3의 언어만
    # 번역합니다.
    has_hangul = bool(_HANGUL_RE.search(subject))
    if target == "ko":
        return subject if has_hangul else (translate_to(subject, "ko") or subject)
    if target == "en":
        return subject if not has_hangul else (translate_to(subject, "en") or subject)
    return translate_to(subject, target) or subject


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
    # 운영자가 읽을 한국어 대역. **모델이 채우지 않습니다** — `schema=` 는 응답을 파싱할
    # 때만 쓰이고(프롬프트가 JSON 모양을 따로 적습니다), 이 칸은 링크·금액 가드가 전부
    # 끝난 뒤 `_draft_reply` 가 채웁니다. 본문이 이미 한국어면 빈 문자열입니다.
    body_ko: str = ""


class _RequestsResult(BaseModel):
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

        # **이미 있는 초안으로 돌아온 작업은 단계로 막지 않습니다.** 이 관문은 「새 초안을
        # 저절로 만들지 마라」는 규칙이지 「이 초안을 마저 쓰지 마라」가 아닙니다. 막으면
        # 리스가 끊겨 돌아온 작업도, 운영자가 누른 「재생성」도 그 자리에서 조용히 멈추고
        # 메시지는 ``drafting`` 인 채로 남습니다. 단계가 정말 넘어갔다면 완성된 초안을
        # ``_retire_superseded_drafts`` 가 곧바로 종료시키므로 나갈 길은 어차피 없습니다.
        expected_stage = settings.HUBSPOT_TICKET_STAGE_NEW.strip()
        if (
            expected_stage
            and not event.get("_draft_message_id")
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

        # Detect the inquiry language once, up front. Every operator-approved reply
        # in this thread must go out in this language; that is enforced in code.
        from ..llm.language import detect_language

        inquiry_lang = detect_language(contact_info.get("last_message", ""), llm=self.llm)
        contact_info["inquiry_language"] = inquiry_lang

        # Persist the inquiry + a "drafting" placeholder up front so the ticket shows
        # on the site immediately, before the (slower) AI reply draft is ready. The
        # placeholder flips to pending_approval once the draft finishes.
        message_id, conv_id, inbound_message_id = self._persist_placeholder(
            contact_info,
            channel,
            inquiry_lang,
            resume_message_id=resume_message_id,
            inbound_job_id=event.get("_inbound_job_id"),
        )

        # 티켓 요약은 **일어난 일마다 한 줄**입니다. 문의가 실제로 저장됐을 때만 붙입니다 —
        # 티켓 하나에 이벤트가 여러 번 오고, 본문을 저장하는 것은 그중 한 번뿐입니다.
        append_summary_line(inbound_message_id)

        # 이 대화에는 이미 회신이 있습니다. 자동 초안은 첫 회신 한 번뿐이고, 이후는 사람이
        # 직접 등록합니다. 고객 문의는 위에서 이미 기록됐으므로 화면에는 그대로 보입니다.
        if message_id is None:
            logger.info(
                "Inbound recorded without a draft — conv %s already has a reply. "
                "이후 회신은 사람이 등록합니다.",
                conv_id,
            )
            return {
                "message_id": None,
                "status": "skipped_reply_exists",
                "object_id": contact_info.get("object_id"),
            }

        # Record the inquiry before the slower AI draft work.
        self._mirror_new_inbound_to_sheet(contact_info, channel, message_id, conv_id)

        # 한국어가 아닌 문의를 지금 옮겨 둡니다. 운영자가 이 티켓을 열 때는 이미 행에
        # 들어 있도록, 아래의 더 느린 초안 작성 전에 처리합니다.
        try:
            cache_korean_inquiries(conversation_id=conv_id)
        except Exception:
            # 못 옮겨도 파이프라인은 갑니다. 화면은 원문을 보여 주고 폴러가 다시 집습니다.
            logger.warning("문의 번역을 미리 해 두지 못했습니다 (conv=%s)", conv_id, exc_info=True)

        # History, deal, and domain research enriches the reviewable draft.
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
            awaiting_review = self._finalize_draft(
                message_id, contact_info, classification, score, draft, conv_id, inquiry_lang
            )
        except Exception:
            # Don't leave the card spinning forever — surface the failure.
            self._mark_draft_failed(message_id)
            raise

        # 고객 요청사항만 새로 뽑습니다(best-effort). 티켓 요약은 위에서 문의 한 줄이
        # 이미 붙었고, 우리 답은 **정말 나갔을 때** send_worker 가 붙입니다.
        self._extract_requests(conv_id, contact_info)

        # The draft is always pending_approval now — the old `if auto_approved: dispatch
        # else: notify` fork went with the auto-approval branch. The one exception is a
        # draft that finished after its ticket had already moved on: it was closed in
        # _finalize_draft, and asking a human to review a closed draft is worse than
        # saying nothing.
        if awaiting_review:
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
                inquiry_key = conv.sheet_inquiry_key
                legacy_inquiry_keys = session.scalars(
                    select(Conversation.sheet_inquiry_key).where(
                        Conversation.id != conv.id,
                        Conversation.sheet_client_id == reserved_client_id,
                        Conversation.sheet_inbound_row.isnot(None),
                        Conversation.sheet_inquiry_key.isnot(None),
                    )
                ).all()

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
            result = record_inbound(
                {
                    "client_id": reserved_client_id,
                    "inquiry_key": inquiry_key,
                    "_legacy_inquiry_keys": legacy_inquiry_keys,
                    "sales_direction": "Inbound",
                    "inquiry_date": when.date().isoformat(),
                    "deal_stage": "New",
                    "deal_stage_detail": "Inquiry",
                    # `pipeline` 은 안 보냅니다 (이관 0104). 그 칸은 구독 플랜을 읽는
                    # 수식이고, 행을 쓴 직후 `_write_pipeline_formula` 가 다시 깝니다 —
                    # 여기서 무엇을 실어 보내든 시트에 남은 적이 없습니다.
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
        """Draft the reply — in the inquiry's language, with a Korean reading beside it.

        Hard rules enforced in CODE here (not left to the model):
        - the draft is in the language it will be SENT in (``ensure_language``), and the
          Korean the operator reviews against is produced once here and stored on the
          row (``korean_reading`` → ``messages.body_ko``);
        - the first reply contains no prices (``strip_price_sentences``);
        - the subject is "RE: <customer subject>" with no duplicate prefixes, in
          the inquiry's language (``reply_subject``) — or, when a policy document
          carries its own mail subject, that subject rendered in the inquiry's
          language (``_subject_in_inquiry_language``). Either way the subject and
          the body the customer receives are in the same language.
        """
        from ..llm.language import language_name
        from ..llm.reply import ensure_language, korean_reading
        from ..llm.translate import is_mostly_korean

        knowledge_docs, doc_subject = select_relevant_docs(
            inquiry=contact_info["last_message"],
            category=classification.category,
            llm=self.llm,
            # 한국어 문의에는 KR 문서, 그 외에는 ENG 문서. 기본 메일 템플릿이 두 벌이라
            # 언어를 안 가리면 두 벌이 같이 붙습니다.
            language=inquiry_lang,
            # 그 문서가 메일 제목을 들고 있으면 같이 받습니다 — 아래 CODE GUARD 3 에서 씁니다.
            with_subject=True,
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
                "reply_format": get_reply_format(inquiry_lang),
                # 초안이 나갈 언어. 참고 문서에 그 언어로 된 완성 메일이 있으면 모델이
                # 그 문장을 그대로 살려 쓸 수 있어야 합니다 — 한 번 한국어를 거치면
                # 그 문장은 되돌아오지 못합니다.
                "reply_language": language_name(inquiry_lang),
            },
            schema=DraftResult,
            tier="pro",
            max_tokens=4000,
        )

        # CODE GUARD 1 — the draft is in the language it will be sent in.
        #
        # **언어 라벨은 결과를 보고 붙입니다.** 번역이 실패하면 본문은 한국어로 남는데,
        # 라벨만 'en' 으로 찍으면 발송 관문이 통과시켜 한국어 메일이 영어 고객에게 갑니다.
        draft.body = ensure_language(draft.body, inquiry_lang, llm=self.llm)
        draft.language = "ko" if is_mostly_korean(draft.body) else (inquiry_lang or "ko")

        # CODE GUARD 1b — links are substituted, never generated. Runs AFTER
        # ensure_language: translation would happily rewrite a URL.
        draft.body = apply_editable_tokens(draft.body, language=inquiry_lang)
        draft.body = canonicalize_contact_links(draft.body, language=inquiry_lang)

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

        # CODE GUARD 3 — the subject is decided HERE, never taken from the model: it is
        # exactly the kind of short line a model invents, and then RE: stacks or the
        # language flips.
        #
        # 근거로 쓴 정책 문서가 메일 제목을 들고 있으면 그것을 씁니다(견적·소개처럼 제목이
        # 정해진 회신). 없으면 예전대로 "RE: <고객이 쓴 제목>" 이고, 그쪽이 고객 메일함에서
        # 원래 스레드에 붙습니다. 어느 쪽이든 검토 화면에서 고칠 수 있습니다.
        #
        # **제목의 언어는 문의의 언어입니다.** 문서 제목은 운영자가 쓴 고정 문장이라 문서의
        # 언어로 나갑니다 — 한국어 문의에 영어 제목이 나간 것이 그것입니다(msg 62).
        draft.subject = _subject_in_inquiry_language(
            doc_subject, contact_info.get("inquiry_language")
        ) or reply_subject(
            contact_info.get("subject"), target_code=contact_info.get("inquiry_language")
        )

        # CODE GUARD 4 — 운영자가 읽을 한국어 대역. **맨 마지막**입니다: 링크 치환·정규화와
        # 금액 가드가 끝난 뒤라야 두 벌이 같은 문장, 같은 링크를 들고 대조가 됩니다.
        # 한 번만 돌고 행에 저장되므로 화면을 열 때마다 모델을 부르지 않습니다.
        draft.body_ko = korean_reading(draft.body, llm=self.llm)
        return draft

    def _persist_placeholder(
        self,
        contact_info: dict,
        channel: str,
        inquiry_lang: str,
        *,
        resume_message_id: int | None = None,
        inbound_job_id: int | None = None,
    ) -> tuple[int | None, int, int | None]:
        """Persist the inquiry and a drafting reply placeholder before the AI draft.

        Returns ``(reply_message_id, conversation_id, inbound_message_id)``.
        ``inbound_message_id`` is set only when this call actually wrote the customer's
        message — one ticket gets several events, and only the first of them stores the
        body. 티켓 요약에 불릿을 덧붙이는 쪽이 그 한 번을 알아야 합니다. The
        card appears on the site immediately as "작성중"; _finalize_draft fills it in
        once the reply is ready.

        ``reply_message_id`` is **None** when this thread already has a reply of its own:
        the automatic draft is the first reply only, and everything after it is registered
        by a person. The customer's message and the progress entry are still written — the
        thread stays complete, it just does not grow a second draft nobody asked for.
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

            # The customer's own subject line = the ticket name. Captured at ingest; it
            # used to be left None here and filled with the AI category later.
            inbound_subject = (contact_info.get("subject") or "").strip() or None

            # Ticket-based inbound: one ticket = one inquiry = one conversation.
            ticket_id = contact_info.get("ticket_id")
            if ticket_id:
                conv = session.query(Conversation).filter_by(hubspot_ticket_id=ticket_id).first()
                if not conv:
                    conv = Conversation(
                        contact_id=contact.id,
                        inquiry_subject=inbound_subject,
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
                        inquiry_subject=inbound_subject,
                        stage="new",
                        inquiry_language=inquiry_lang,
                    )
                    session.add(conv)
                    session.flush()

            # The thread language is set from the first inbound and kept stable.
            if not conv.inquiry_language and inquiry_lang:
                conv.inquiry_language = inquiry_lang
            # Same repair for the ticket name. It used to be written by the constructor
            # only, so a row created by an event that carried no subject — a ticket fetch
            # that failed, a contact-only webhook — stayed nameless forever even though
            # every later event for that ticket had it. One ticket gets several events.
            if not conv.inquiry_subject and inbound_subject:
                conv.inquiry_subject = inbound_subject

            # First inbound in the thread? (count BEFORE inserting this one.)
            prior_inbound = (
                session.query(Message)
                .filter(Message.conversation_id == conv.id, Message.direction == "inbound")
                .count()
            )
            is_first_inbound = prior_inbound == 0

            # Snapshot the inbound body so the approval UI shows what we're replying to
            # (the subject is kept separate — fixes "가끔 제목만 오더라").
            inbound_body = (contact_info.get("last_message") or "").strip()
            inbound_message_id: int | None = None
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
                inquiry = Message(
                    conversation_id=conv.id,
                    direction="inbound",
                    channel=channel,
                    from_address=email or None,
                    to_address=None,
                    subject=inbound_subject,
                    body=inbound_body,
                    language=inquiry_lang or "en",
                    status="received",
                )
                session.add(inquiry)
                conv.last_incoming_at = datetime.now(timezone.utc)
                session.flush()
                inbound_message_id = inquiry.id
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
            resumable = bool(
                msg
                and msg.conversation_id == conv.id
                and msg.direction == "outgoing"
                and msg.prompt_variant != "auto_ack"
                and msg.status in {"drafting", "draft_failed"}
            )
            # 자동 초안은 **한 대화에 하나**입니다. 두 번째 문의부터는 사람이 직접 회신을
            # 등록합니다 — 운영자의 결정입니다.
            #
            # 조건이 "두 번째 inbound" 가 아니라 "이미 회신 줄기가 있다" 인 이유: 티켓 하나에
            # 이벤트가 여러 번 옵니다(웹훅 + 10분 폴러 + 티켓 변경). 고객이 두 번 썼을 때만
            # 막으면, 같은 첫 문의가 다시 들어와 초안이 하나 더 생깁니다. 접수확인은 회신이
            # 아니므로 세지 않습니다.
            #
            # resumable 이 먼저입니다: 죽은 durable job 이 자기 초안을 이어 쓰는 것은 두 번째
            # 초안이 아니라 같은 초안입니다.
            if not resumable:
                prior_reply = (
                    session.query(Message.id)
                    .filter(
                        Message.conversation_id == conv.id,
                        Message.direction == "outgoing",
                        (Message.prompt_variant.is_(None))
                        | (Message.prompt_variant != "auto_ack"),
                    )
                    .first()
                    is not None
                )
                if prior_reply:
                    session.commit()  # 고객 문의와 진행 기록은 남깁니다.
                    return None, conv.id, inbound_message_id
            if not resumable:
                msg = Message(
                    conversation_id=conv.id,
                    direction="outgoing",
                    channel=channel,
                    from_address=None,
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

            if inbound_job_id:
                job = session.get(InboundJob, inbound_job_id)
                if job and job.status == "processing":
                    payload = dict(job.payload or {})
                    payload["draft_message_id"] = msg.id
                    job.payload = payload
            session.commit()
            return msg.id, conv.id, inbound_message_id
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
        """Finalize the draft. Returns whether it is actually waiting for a human.

        False means the ticket moved past New while we were writing (미팅 링크 발송,
        HubSpot 에서의 답변, 콘솔 보드 이동) and the finished draft was closed on the
        spot instead of being put in 발송 대기. That is the one window the stage gates
        cannot cover: ``handle()`` checked the stage minutes ago, at ingest.
        """
        session = SessionLocal()
        try:
            msg = session.get(Message, message_id)
            if not msg:
                return False
            msg.subject = draft.subject
            msg.body = draft.body
            # 이 초안의 한국어 대역. **매번 새로 씁니다** — 「초안 다시 쓰기」로 들어오면
            # 지난 초안의 대역이 남아 있고, 그러면 화면이 새 초안 옆에 다른 초안을
            # 「한국어」라며 붙입니다. 본문이 이미 한국어면 대역은 없습니다.
            msg.body_ko = draft.body_ko or None
            msg.language = draft.language or "ko"
            msg.target_language = inquiry_lang or msg.target_language
            # Every reply waits for a human. The old score-based auto-approval branch
            # and the immediate receipt acknowledgement have both been removed, so no
            # configuration value can make an inbound message send by itself.
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

            # 초안을 쓰는 동안 단계가 움직였으면 여기서 바로 종료합니다. flush 가 먼저인
            # 이유: SessionLocal 은 autoflush=False 라, 위에서 준 pending_approval 이
            # DB 에 없으면 아래 조회가 이 행을 못 봅니다.
            session.flush()
            retired = bool(conv and _retire_superseded_drafts(session, conv.id, conv.stage))
            session.commit()
            return not retired
        finally:
            session.close()

    # ----- Customer requests -----

    def _extract_requests(self, conv_id: int | None, contact_info: dict) -> None:
        """고객이 명시적으로 요청한 것만 뽑아 `customer_requests` 에 넣습니다(best-effort).

        **고객이 쓴 글만 읽습니다.** 예전에는 이 자리에서 대화 전체를 읽어 요약 문단까지
        같이 썼는데, 이 호출은 초안이 만들어진 **직후**에 돌고 초안도 메시지 행이라 아무도
        보내지 않은 글이 「우리가 이렇게 답했다」로 요약에 들어갔습니다(2026-08-20 지적).
        요약은 이제 `summaries.append_summary_line` 이 실제로 일어난 일마다 한 줄씩
        덧붙입니다 — 여기서는 요청사항만 봅니다.
        """
        if not conv_id:
            return
        try:
            with SessionLocal() as session:
                rows = (
                    session.query(Message)
                    .filter(
                        Message.conversation_id == conv_id,
                        Message.direction == "inbound",
                    )
                    .order_by(Message.created_at.asc(), Message.id.asc())
                    .all()
                )
                # 긴 대화에서는 **최근** 문의부터 담습니다. 앞에서 자르면 지금 답하려는
                # 바로 그 메시지가 빠집니다.
                newest_first: list[str] = []
                used = 0
                for m in reversed(rows):
                    if not (m.body or "").strip():
                        continue
                    subj = f"[{m.subject}] " if m.subject else ""
                    part = f"고객: {subj}{m.body.strip()}"
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
                "inbound/extract_requests",
                {
                    "contact_name": contact_info.get("full_name", ""),
                    "company": contact_info.get("company", ""),
                    "thread_text": thread_text,
                },
                schema=_RequestsResult,
                tier="flash",
                max_tokens=1200,
            )
            with SessionLocal() as session:
                conv = session.get(Conversation, conv_id)
                if conv:
                    conv.customer_requests = (
                        result.customer_requests or ""
                    ).strip() or conv.customer_requests
                    session.commit()
        except Exception:
            logger.warning(
                "Customer-request extraction failed for conv %s (non-fatal).",
                conv_id,
                exc_info=True,
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


def cache_korean_inquiries(limit: int = 20, conversation_id: int | None = None) -> int:
    """한국어가 아닌 **고객 문의**를 한국어로 옮겨 행에 넣어 둡니다. 처리한 건수를 돌려줍니다.

    번역을 미리 해 두는 것은 이것 하나뿐입니다. 회신 초안은 원래 한국어로 쓰이고, 보낼
    언어로 바꾸는 것은 운영자가 검토 화면에서 `번역하기` 를 누를 때입니다 — 미리 할 이유가
    없고, 운영자가 고친 본문을 번역해야 하므로 미리 할 수도 없습니다.

    **화면을 여는 길에서는 절대 부르지 않습니다.** 예전에는 티켓을 열 때 번역했는데, 그러면
    그 티켓을 처음 여는 사람이 말풍선마다 모델을 기다렸다가 화면을 봤습니다(영어 세 줄이면
    여섯 번). 답을 쓰려고 여는 창에서 할 일이 아닙니다.

    두 곳에서 부릅니다: 접수 직후 그 대화만(``conversation_id``), 그리고 10분 폴러가
    한도만큼(옛 행과 그때 실패한 것을 조금씩 메웁니다). 어느 쪽도 결과를 기다리는 사람이
    없습니다.

    **모델이 안 되면 비워 둡니다.** ``to_korean`` 은 실패해도 예외를 던지지 않고 빈 문자열을
    돌려주므로(translate.py), 그 자리에 원문을 넣으면 영어가 「한국어 번역」이라는 이름을 달고
    행에 굳고 폴러는 그 행을 다시 집지 않습니다 — 되돌릴 방법이 없습니다(검토 화면의
    `번역하기` 는 **회신 초안**을 보낼 언어로 바꾸는 버튼이라 고객 문의에는 안 닿습니다).
    NULL 은 "아직 안 옮겼다" 하나만 뜻하게 두고, 다음 순회에 다시 집히게 합니다. 영원히
    도는 것은 아닙니다: 이미 한국어거나 글자가 없는 본문은 아래 else 로 빠져 값이 채워지고,
    남는 것은 진짜 모델 장애뿐이라 10분에 ``limit`` 건으로 묶여 있습니다.
    """
    from ..llm.translate import needs_korean, to_korean

    with SessionLocal() as session:
        query = session.query(Message).filter(
            Message.direction == "inbound",
            Message.body_ko.is_(None),
        )
        if conversation_id is not None:
            query = query.filter(Message.conversation_id == conversation_id)
        rows = query.order_by(Message.id.desc()).limit(limit).all()

        done = 0
        for row in rows:
            try:
                body = row.body or ""
                row.body_ko = (to_korean(body) or None) if needs_korean(body) else body
                subject = row.subject
                if subject and row.subject_ko is None:
                    row.subject_ko = (
                        (to_korean(subject) or None) if needs_korean(subject) else subject
                    )
                done += 1
            except Exception:
                # 한 건이 막혀도 나머지는 채웁니다. 다음 순회에서 다시 집힙니다.
                logger.warning("문의 번역 실패 (msg=%s)", row.id, exc_info=True)
        if done:
            session.commit()
    return done
