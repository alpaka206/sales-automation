"""Send dispatcher - routes to HubSpot, SMTP, or WhatsApp based on channel and settings."""

from __future__ import annotations

import logging

from ...common.config import settings
from ...db.models import Message
from .smtp import send_smtp
from .whatsapp import send_whatsapp

logger = logging.getLogger(__name__)


async def send(message: Message) -> None:
    """Send a message via the appropriate provider."""
    if message.channel == "whatsapp":
        send_whatsapp(message)
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
