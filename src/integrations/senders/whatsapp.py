"""WhatsApp Cloud API sender — gated behind WHATSAPP_ENABLED flag."""

from __future__ import annotations

import logging

import httpx

from ...common.config import settings
from ...db.models import Message

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.facebook.com/v19.0"


def _masked_phone(phone: str) -> str:
    return f"***{phone[-4:]}" if phone else "(missing)"


class WhatsAppDisabled(RuntimeError):
    """Raised when WhatsApp sending is attempted but the feature is disabled."""
    pass


class WhatsAppSendError(RuntimeError):
    """Raised when the WhatsApp API returns an error."""
    pass


def _require_enabled() -> None:
    if not settings.WHATSAPP_ENABLED:
        raise WhatsAppDisabled("WhatsApp is disabled (WHATSAPP_ENABLED=false).")
    if not settings.WHATSAPP_ACCESS_TOKEN:
        raise WhatsAppSendError("WHATSAPP_ACCESS_TOKEN not set.")
    if not settings.WHATSAPP_PHONE_NUMBER_ID:
        raise WhatsAppSendError("WHATSAPP_PHONE_NUMBER_ID not set.")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def _api_url() -> str:
    return f"{GRAPH_API_URL}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"


async def send_whatsapp_template(
    phone: str,
    template_name: str | None = None,
    language_code: str = "ko",
    params: list[str] | None = None,
) -> str:
    """Send a WhatsApp template message. Returns the message ID from Meta."""
    _require_enabled()
    tpl = template_name or settings.WHATSAPP_TEMPLATE_NAME

    body: dict = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": tpl,
            "language": {"code": language_code},
        },
    }

    if params:
        body["template"]["components"] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in params],
            }
        ]

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(_api_url(), json=body, headers=_headers())

    data = r.json()
    if r.status_code != 200:
        error_code = data.get("error", {}).get("code", 0)
        error_msg = data.get("error", {}).get("message", r.text)
        if error_code == 131009:
            raise WhatsAppSendError(f"Invalid WhatsApp recipient: {phone}")
        raise WhatsAppSendError(f"WhatsApp API error ({error_code}): {error_msg}")

    msg_id = data.get("messages", [{}])[0].get("id", "")
    logger.info("WhatsApp template '%s' sent to %s (msg_id=%s)", tpl, _masked_phone(phone), msg_id)
    return msg_id


async def send_whatsapp_freeform(phone: str, text: str) -> str:
    """Send a freeform WhatsApp text message (only within 24h reply window)."""
    _require_enabled()

    body = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text},
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(_api_url(), json=body, headers=_headers())

    data = r.json()
    if r.status_code != 200:
        error_code = data.get("error", {}).get("code", 0)
        error_msg = data.get("error", {}).get("message", r.text)
        raise WhatsAppSendError(f"WhatsApp API error ({error_code}): {error_msg}")

    msg_id = data.get("messages", [{}])[0].get("id", "")
    logger.info("WhatsApp freeform sent to %s (msg_id=%s)", _masked_phone(phone), msg_id)
    return msg_id


async def send_whatsapp(message: Message) -> None:
    """Send a WhatsApp message for an approved Message record."""
    _require_enabled()

    phone = message.to_address
    if not phone:
        raise WhatsAppSendError("No phone number (to_address) on message.")

    await send_whatsapp_template(
        phone=phone,
        params=[message.body[:1024]],
    )
