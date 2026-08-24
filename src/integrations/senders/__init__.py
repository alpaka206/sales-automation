"""Send inbound replies through SMTP."""

from __future__ import annotations

import asyncio
import logging

from ...common.textwash import text_wash
from ...db.models import Message
from .smtp import send_smtp

logger = logging.getLogger(__name__)


class SendLanguageMismatch(RuntimeError):
    """Raised when an approved message has not completed operator-reviewed translation."""


def _canonicalize_reply_links(message: Message, language: str) -> None:
    if getattr(message, "prompt_variant", None) == "auto_ack":
        return
    if not isinstance(message.body, str):
        return
    from ...llm.prompts import canonicalize_contact_links

    message.body = text_wash(canonicalize_contact_links(message.body, language))


def enforce_send_language(message: Message) -> None:
    """Final guard: only an already reviewed target-language body may leave.

    The operator's hard rule is that a reply must go out in the inquiry's language.
    Our code sets ``message.language`` at every step (draft = 'ko', translate button
    = target), and ``message.target_language`` holds
    the language it MUST be sent in. So:

    - every reply body is whitespace/format-normalized (text wash);
    Translation belongs to the explicit review-screen button. If an old API client or
    stale approved row bypasses the approval guard, fail closed instead of translating
    unseen text during delivery.
    """
    if isinstance(message.body, str):
        message.body = text_wash(message.body)

    target = message.target_language if isinstance(message.target_language, str) else ""
    target = target.lower()
    if not target:
        language = getattr(message, "language", "")
        _canonicalize_reply_links(message, language if isinstance(language, str) else "")
        return
    current = message.language if isinstance(message.language, str) else ""
    current = current.lower()

    from ...llm.translate import is_mostly_korean

    if current != target or (target != "ko" and is_mostly_korean(message.body)):
        raise SendLanguageMismatch(
            f"message {message.id} requires reviewed translation "
            f"(current={current or '?'}, target={target})"
        )
    _canonicalize_reply_links(message, target)


def enforce_first_reply_no_price(message: Message) -> None:
    """Final code guard for the "no price in the FIRST reply" rule.

    The draft-time strip can be bypassed (operator types a price into the draft, or
    the translate step re-renders one), so we re-strip prices here — the single send
    chokepoint — when this is the first real reply in the thread. We skip the
    auto-ack. Runs AFTER translation so a translated-in price is caught too.
    """
    if not isinstance(message.target_language, str) or not message.target_language:
        return
    if getattr(message, "prompt_variant", None) == "auto_ack":
        return
    conv_id = getattr(message, "conversation_id", None)
    if not isinstance(conv_id, int):
        return

    from ...db.models import Message as _Message
    from ...db.session import SessionLocal

    try:
        with SessionLocal() as session:
            prior_sent = (
                session.query(_Message)
                .filter(
                    _Message.conversation_id == conv_id,
                    _Message.direction == "outgoing",
                    _Message.status == "sent",
                    _Message.id != message.id,
                    (_Message.prompt_variant.is_(None)) | (_Message.prompt_variant != "auto_ack"),
                )
                .count()
            )
    except Exception:
        logger.warning("First-reply price guard: conv lookup failed; skipping.", exc_info=True)
        return
    if prior_sent:
        return  # not the first reply — later replies may quote KB prices

    from ...common.pricing_guard import strip_price_sentences

    cleaned, removed = strip_price_sentences(message.body)
    if removed:
        message.body = cleaned
        logger.warning(
            "Send guard: stripped %d price line(s) from the FIRST reply (msg %s): %s",
            len(removed),
            message.id,
            " | ".join(removed)[:200],
        )


async def _log_hubspot_email(message: Message, row: Message | None = None) -> None:
    """Best-effort timeline log after SMTP delivery; never reverses a real send.

    ``row`` is the ORM record whose relationships are used and whose engagement id is
    stamped. It is accepted separately so this helper remains safe for detached callers.
    """
    record = row if row is not None else message
    try:
        conversation = record.conversation
        contact = conversation.contact if conversation else None
        contact_id = contact.hubspot_contact_id if contact else None
        ticket_id = conversation.hubspot_ticket_id if conversation else None
    except Exception:
        contact_id, ticket_id = None, None
    if not contact_id:
        return

    from ..hubspot import HubSpotClient, HubSpotNotConfigured

    client = None
    try:
        client = HubSpotClient()
        record.hubspot_engagement_id = await client.create_email_engagement(
            contact_id=contact_id,
            subject=message.subject or "",
            body=message.body or "",
            ticket_id=ticket_id,
        )
    except HubSpotNotConfigured:
        logger.info("HubSpot is not configured; skipping timeline log for message %d.", message.id)
    except Exception:
        logger.warning(
            "HubSpot timeline log failed for message %d; SMTP send succeeded.",
            message.id,
            exc_info=True,
        )
    finally:
        if client is not None:
            await client.close()


async def send(message: Message) -> None:
    """Send a message via SMTP, then record it on the HubSpot timeline.

    HubSpot is not the configured transport here. Transactional Single-Send requires a
    paid add-on, ``transactional-email`` scope, and a designed template. The CRM email
    object written at the end is only the customer-history record.
    """
    row = message

    # Code-enforced language + text wash, then the first-reply no-price rule.
    if message.direction == "outgoing":
        enforce_send_language(message)
        enforce_first_reply_no_price(message)

    # SMTP is the only delivery channel; HubSpot receives a timeline copy afterwards.
    await asyncio.to_thread(send_smtp, message)
    logger.info("Message %d sent via smtp.", message.id)

    await _log_hubspot_email(message, row)
