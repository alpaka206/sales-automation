"""Send inbound replies through SMTP and optionally mirror them to WhatsApp."""

from __future__ import annotations

import asyncio
import copy
import logging

from ...common.config import settings
from ...common.textwash import text_wash
from ...db.models import Message
from .smtp import send_smtp
from .whatsapp import WhatsAppDisabled, send_whatsapp, send_whatsapp_template

logger = logging.getLogger(__name__)


def enforce_send_language(message: Message) -> None:
    """Final code guard: a customer reply leaves in the right language, washed.

    The operator's hard rule is that a reply must go out in the inquiry's language.
    Our code sets ``message.language`` at every step (draft = 'ko', translate button
    = target, auto-ack = its final language), and ``message.target_language`` holds
    the language it MUST be sent in. So:

    - every reply body is whitespace/format-normalized (text wash);
    - if a target is set and the body isn't in it yet (e.g. the operator hit send on
      the Korean draft without translating), it is translated to the target here so
      a wrong-language reply can never leave.

    Translation failures are logged and the body is sent as-is rather than dropped.
    """
    if message.channel == "whatsapp":
        return
    if isinstance(message.body, str):
        message.body = text_wash(message.body)

    target = message.target_language if isinstance(message.target_language, str) else ""
    target = target.lower()
    if not target:
        return
    current = message.language if isinstance(message.language, str) else ""
    current = current.lower()

    from ...llm.translate import needs_korean, translate_to

    if current == target:
        # Metadata says we're already in the target. Trust it, EXCEPT the cheap
        # script sanity check: if the target isn't Korean yet the body is actually
        # predominantly Korean (e.g. the operator translated, then re-typed Korean),
        # the metadata is stale — fall through and translate. No LLM call here.
        if not (target != "ko" and not needs_korean(message.body)):
            return

    translated = translate_to(message.body, target)
    if translated:
        message.body = text_wash(translated)
        message.language = target
        logger.info(
            "Send guard: translated message %s body from '%s' to target '%s'.",
            message.id,
            current or "?",
            target,
        )
    else:
        logger.warning(
            "Send guard: translation of message %s to '%s' failed; sending as-is ('%s').",
            message.id,
            target,
            current or "?",
        )


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


def _record_whatsapp_result(
    message_id: int, attempted: bool, sent: bool, error: str | None = None
) -> None:
    """Persist WhatsApp delivery result to the messages table."""
    from ...db.session import SessionLocal

    try:
        with SessionLocal() as session:
            msg = session.get(Message, message_id)
            if msg:
                msg.whatsapp_attempted = attempted
                msg.whatsapp_sent = sent
                msg.whatsapp_error = error
                session.commit()
    except Exception:
        logger.warning("Failed to record WhatsApp result for message %d", message_id, exc_info=True)


async def _try_whatsapp_template(message: Message) -> None:
    """Attempt a WhatsApp template send alongside email, recording result in DB.

    Always fetches the phone from the Contact record — never reuses the email
    in message.to_address. WhatsApp Cloud API has no native "does this number
    have WhatsApp?" lookup, so we send the template and let the API return
    error 131009 if the number is not on WhatsApp (handled in whatsapp.py).
    """
    from ...db.session import SessionLocal
    from ...db.models import Contact

    phone: str | None = None
    try:
        with SessionLocal() as session:
            stored = session.get(Message, message.id)
            if stored and stored.whatsapp_sent:
                logger.info("WhatsApp already sent for message %d; skipping replay.", message.id)
                return
            c = session.get(Contact, message.conversation.contact_id)
            if c and c.whatsapp_opt_in:
                phone = getattr(c, "phone", None)
    except Exception:
        logger.warning(
            "Failed to look up WhatsApp phone for contact %d",
            message.conversation.contact_id,
            exc_info=True,
        )

    if not phone:
        return

    _record_whatsapp_result(message.id, attempted=True, sent=False)

    try:
        await send_whatsapp_template(
            phone=phone,
            language_code=(message.target_language or message.language or "ko")[:8],
            params=[message.body[:1024]],
        )
        _record_whatsapp_result(message.id, attempted=True, sent=True)
        logger.info("WhatsApp template also sent for message %d.", message.id)
    except WhatsAppDisabled:
        _record_whatsapp_result(message.id, attempted=False, sent=False)
        logger.info("WhatsApp disabled, skipping template send for message %d.", message.id)
    except Exception as exc:
        _record_whatsapp_result(message.id, attempted=True, sent=False, error=str(exc))
        logger.warning(
            "WhatsApp template send failed for message %d, email unaffected.",
            message.id,
            exc_info=True,
        )


async def _log_hubspot_email(message: Message) -> None:
    """Best-effort timeline log after SMTP delivery; never reverses a real send."""
    try:
        contact = message.conversation.contact
        contact_id = contact.hubspot_contact_id if contact else None
    except Exception:
        contact_id = None
    if not contact_id:
        return

    from ..hubspot import HubSpotClient, HubSpotNotConfigured

    client = None
    try:
        client = HubSpotClient()
        message.hubspot_engagement_id = await client.create_email_engagement(
            contact_id=contact_id,
            subject=message.subject or "",
            body=message.body or "",
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
    """Send a message via the appropriate provider.

    Email failure raises (caller handles). WhatsApp failure is logged but does not
    affect the email outcome.
    """
    override = settings.SEND_OVERRIDE_EMAIL.strip()

    if message.channel == "whatsapp":
        if override:
            logger.info(
                "TEST MODE (SEND_OVERRIDE_EMAIL set): skipping WhatsApp send for message %d.",
                message.id,
            )
            return
        await send_whatsapp(message)
        return

    # Test-mode redirect: reroute every customer-facing email to one address and
    # force SMTP. Real HubSpot contacts are not touched in this mode.
    if override:
        # Never mutate the ORM row: test delivery must not replace the real recipient
        # or reviewed subject that operators see in the console.
        message = copy.copy(message)
        original = message.to_address or "(none)"
        message.to_address = override
        if message.subject and not message.subject.startswith("[TEST"):
            message.subject = f"[TEST→{original}] {message.subject}"
        logger.info(
            "TEST MODE: redirecting message %d from %s to %s (forcing SMTP).",
            message.id,
            original,
            override,
        )

    # Code-enforced language + text wash, then the first-reply no-price rule.
    if message.direction == "outgoing":
        enforce_send_language(message)
        enforce_first_reply_no_price(message)

    # When a branded HTML signature (or "none") is selected, strip the LLM's
    # default text signature so it doesn't render twice.
    from ..email_html import strip_known_signature, strips_text_signature

    if (
        message.direction == "outgoing"
        and isinstance(message.body, str)
        and strips_text_signature(getattr(message, "signature_key", None))
    ):
        message.body = strip_known_signature(message.body)

    # SMTP is the only delivery channel; HubSpot receives a timeline copy afterwards.
    await asyncio.to_thread(send_smtp, message)
    logger.info("Message %d sent via smtp.", message.id)

    if not override:
        await _log_hubspot_email(message)

    # WhatsApp piggyback — best-effort, never breaks the email flow.
    # Skipped entirely in test mode so no real phone is messaged.
    if (
        settings.WHATSAPP_ENABLED
        and not override
        and getattr(message, "prompt_variant", None) != "auto_ack"
    ):
        await _try_whatsapp_template(message)
