"""Send reviewed inbound replies through HubSpot Conversations."""

from __future__ import annotations

import logging
from email.utils import getaddresses

from ...common.textwash import text_wash
from ...db.models import Message
from ..delivery import DeliveryPermanentError, SendingDisabled

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
    Our code sets ``message.language`` at every step (draft = the language it was
    actually written in, translate button = target), and ``message.target_language``
    holds the language it MUST be sent in. So:

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


async def send(message: Message) -> None:
    """Reply on the ticket's existing HubSpot Conversations email thread."""
    from ...common.safe_mode import email_delivery_enabled

    if not email_delivery_enabled():
        raise SendingDisabled(
            "Email delivery is disabled: enable LIVE_EXTERNAL_WRITES and the "
            "code-level EMAIL_SENDING_ENABLED switch."
        )

    # Code-enforced language + text wash, then the first-reply no-price rule.
    if message.direction == "outgoing":
        enforce_send_language(message)
        enforce_first_reply_no_price(message)

    if any(char in (message.subject or "") for char in ("\r", "\n")):
        raise DeliveryPermanentError("Email subject contains illegal CR/LF characters")
    if any(char in (message.to_address or "") for char in ("\r", "\n")):
        raise DeliveryPermanentError("Recipient contains illegal CR/LF characters")
    recipients = [address for _name, address in getaddresses([message.to_address or ""]) if address]
    if len(recipients) != 1 or "@" not in recipients[0]:
        raise DeliveryPermanentError("Exactly one valid recipient email is required")

    try:
        ticket_id = message.conversation.hubspot_ticket_id
    except Exception as exc:
        raise DeliveryPermanentError("The message has no loaded HubSpot ticket") from exc
    if not ticket_id:
        raise DeliveryPermanentError("The message has no HubSpot ticket ID")

    from ..email_html import branded_signature_html, to_html_email
    from ..hubspot import HubSpotClient

    signature_html = branded_signature_html(getattr(message, "signature_key", None))
    rich_text = to_html_email(message.body or "", signature_html=signature_html)
    client = HubSpotClient()
    try:
        context = await client.find_conversation_reply_context(ticket_id, recipients[0])
        hubspot_message_id = await client.send_conversation_message(
            context,
            recipient_email=recipients[0],
            subject=message.subject or "",
            text=message.body or "",
            rich_text=rich_text,
        )
    finally:
        await client.close()

    message.hubspot_thread_id = context.thread_id
    message.hubspot_message_id = hubspot_message_id
    logger.info(
        "Message %d sent through HubSpot Conversations (thread=%s, message=%s).",
        message.id,
        context.thread_id,
        hubspot_message_id,
    )
