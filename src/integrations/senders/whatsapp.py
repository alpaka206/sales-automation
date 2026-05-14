"""WhatsApp sender - stub until Meta Business approval."""

from __future__ import annotations

import logging

from ...common.config import settings
from ...db.models import Message

logger = logging.getLogger(__name__)


def send_whatsapp(message: Message) -> None:
    """Send a WhatsApp message. Raises NotImplementedError unless WHATSAPP_ENABLED=true."""
    if not settings.WHATSAPP_ENABLED:
        raise NotImplementedError(
            "WhatsApp sending is disabled (WHATSAPP_ENABLED=false). "
            "Enable it and set WHATSAPP_ACCESS_TOKEN after Meta Business approval."
        )
    if not settings.WHATSAPP_ACCESS_TOKEN:
        raise RuntimeError("WHATSAPP_ACCESS_TOKEN not set.")

    logger.warning("WhatsApp send stub called for message %d - not yet implemented.", message.id)
    raise NotImplementedError("WhatsApp Cloud API integration not yet implemented.")
