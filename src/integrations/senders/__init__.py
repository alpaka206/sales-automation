"""Send dispatcher - routes to HubSpot, SMTP, or WhatsApp based on channel and settings."""

from __future__ import annotations

import logging

from ...common.config import settings
from ...db.models import Message
from .smtp import send_smtp
from .whatsapp import WhatsAppDisabled, send_whatsapp, send_whatsapp_template

logger = logging.getLogger(__name__)


async def _try_whatsapp_template(message: Message) -> None:
    """Attempt a WhatsApp template send alongside email, failing silently."""
    phone = getattr(message, "to_address", None)
    if not phone:
        contact = getattr(message, "conversation", None)
        if contact:
            from ...db.session import SessionLocal
            from ...db.models import Contact

            with SessionLocal() as session:
                c = session.get(Contact, message.conversation.contact_id)
                if c and c.whatsapp_opt_in:
                    phone = getattr(c, "phone", None) if hasattr(c, "phone") else None

    if not phone:
        return

    try:
        await send_whatsapp_template(
            phone=phone,
            params=[message.body[:1024]],
        )
        logger.info("WhatsApp template also sent for message %d.", message.id)
    except WhatsAppDisabled:
        logger.info("WhatsApp disabled, skipping template send for message %d.", message.id)
    except Exception:
        logger.warning("WhatsApp template send failed for message %d, email unaffected.", message.id, exc_info=True)


async def send(message: Message) -> None:
    """Send a message via the appropriate provider."""
    if message.channel == "whatsapp":
        await send_whatsapp(message)
        return

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

    if settings.WHATSAPP_ENABLED:
        await _try_whatsapp_template(message)
