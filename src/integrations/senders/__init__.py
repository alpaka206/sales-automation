"""Send dispatcher - routes to HubSpot, SMTP, or WhatsApp based on channel and settings."""

from __future__ import annotations

import logging

from ...common.config import settings
from ...db.models import Message
from .smtp import send_smtp
from .whatsapp import WhatsAppDisabled, send_whatsapp, send_whatsapp_template

logger = logging.getLogger(__name__)


def _record_whatsapp_result(message_id: int, attempted: bool, sent: bool, error: str | None = None) -> None:
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
    """Attempt a WhatsApp template send alongside email, recording result in DB."""
    phone = message.to_address
    if not phone:
        from ...db.session import SessionLocal
        from ...db.models import Contact

        try:
            with SessionLocal() as session:
                c = session.get(Contact, message.conversation.contact_id)
                if c and c.whatsapp_opt_in:
                    phone = getattr(c, "phone", None)
        except Exception:
            pass

    if not phone:
        return

    _record_whatsapp_result(message.id, attempted=True, sent=False)

    try:
        await send_whatsapp_template(
            phone=phone,
            params=[message.body[:1024]],
        )
        _record_whatsapp_result(message.id, attempted=True, sent=True)
        logger.info("WhatsApp template also sent for message %d.", message.id)
    except WhatsAppDisabled:
        _record_whatsapp_result(message.id, attempted=False, sent=False)
        logger.info("WhatsApp disabled, skipping template send for message %d.", message.id)
    except Exception as exc:
        _record_whatsapp_result(message.id, attempted=True, sent=False, error=str(exc))
        logger.warning("WhatsApp template send failed for message %d, email unaffected.", message.id, exc_info=True)


async def send(message: Message) -> None:
    """Send a message via the appropriate provider.

    Email failure raises (caller handles). WhatsApp failure is logged but does not
    affect the email outcome.
    """
    if message.channel == "whatsapp":
        await send_whatsapp(message)
        return

    # Email send — failure raises, propagating to caller
    if settings.EMAIL_PROVIDER == "smtp":
        send_smtp(message)
    elif settings.EMAIL_PROVIDER == "hubspot":
        from ...integrations.hubspot import HubSpotClient, HubSpotNotConfigured

        try:
            hs = HubSpotClient()
            await hs.send_email(
                contact_id=str(message.conversation.contact_id),
                subject=message.subject or "",
                body=message.body,
                from_email=settings.SMTP_FROM_EMAIL,
            )
            await hs.close()
        except HubSpotNotConfigured:
            logger.warning("HubSpot not configured, falling back to SMTP.")
            send_smtp(message)
    else:
        raise ValueError(f"Unknown EMAIL_PROVIDER: {settings.EMAIL_PROVIDER}")

    logger.info("Message %d sent via %s.", message.id, settings.EMAIL_PROVIDER)

    # WhatsApp piggyback — best-effort, never breaks the email flow
    if settings.WHATSAPP_ENABLED:
        await _try_whatsapp_template(message)
