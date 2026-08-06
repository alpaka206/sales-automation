"""Send inbound replies through SMTP."""

from __future__ import annotations

import asyncio
import copy
import logging

from ...common.textwash import text_wash
from ...db.models import Message
from .smtp import send_smtp

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
    if isinstance(message.body, str):
        message.body = text_wash(message.body)

    target = message.target_language if isinstance(message.target_language, str) else ""
    target = target.lower()
    if not target:
        return
    current = message.language if isinstance(message.language, str) else ""
    current = current.lower()

    from ...llm.translate import is_mostly_korean, translate_to

    if current == target:
        # Metadata says we're already in the target. Trust it, EXCEPT the cheap
        # script sanity check: if the target isn't Korean yet the body is actually
        # predominantly Korean (e.g. the operator translated, then re-typed Korean),
        # the metadata is stale — fall through and translate. No LLM call here.
        if not (target != "ko" and is_mostly_korean(message.body)):
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


async def _log_hubspot_email(message: Message, row: Message | None = None) -> None:
    """Best-effort timeline log after SMTP delivery; never reverses a real send.

    ``message`` is what actually left (in test mode a copy whose subject carries the
    ``[TEST→…]`` marker, so the timeline says what really happened). ``row`` is the ORM
    record: relationships are read from it and the engagement id is stamped on it, because
    the copy shares session state and may not be able to load either.
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

    HubSpot is not a transport here and cannot be: it has no API that sends this reply
    (the transactional single-send needs a paid add-on and a designed template). The CRM
    email object it writes at the end IS the customer history.
    """
    from ...common.safe_mode import resolve_send_override

    # The ORM record the caller will commit. `message` is rebound to a copy below when
    # the mail is rerouted, and the copy must not be what carries the engagement id.
    row = message

    # In pre-launch safe mode this is ALWAYS non-empty (forces ronald@…), so every
    # branch below that keys off `override` reroutes the mail.
    override = resolve_send_override()

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

    # SMTP is the only delivery channel; HubSpot receives a timeline copy afterwards.
    await asyncio.to_thread(send_smtp, message)
    logger.info("Message %d sent via smtp.", message.id)

    # Logged for a rerouted send too. Skipping it used to leave the customer's history
    # with a gap for every test send — and while FORCE_TEST_RECIPIENT is pinned on, that
    # is every send there is. What goes on the timeline is what actually left, subject
    # marker and all, so a test copy can never read as a real reply to the customer.
    await _log_hubspot_email(message, row)
