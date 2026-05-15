"""Tests for WhatsApp Cloud API sender."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.integrations.senders.whatsapp import (
    WhatsAppDisabled,
    WhatsAppSendError,
    send_whatsapp,
    send_whatsapp_freeform,
    send_whatsapp_template,
)


def _make_message(channel: str = "whatsapp", to_address: str = "+821012345678", body: str = "Hello") -> MagicMock:
    msg = MagicMock()
    msg.id = 1
    msg.channel = channel
    msg.to_address = to_address
    msg.body = body
    msg.subject = "Test"
    return msg


# ---------- Feature flag ----------


@pytest.mark.asyncio
async def test_disabled_raises_whatsapp_disabled():
    """When WHATSAPP_ENABLED=false, WhatsAppDisabled should be raised."""
    from src.common.config import settings

    with patch.object(settings, "WHATSAPP_ENABLED", False):
        with pytest.raises(WhatsAppDisabled):
            await send_whatsapp(_make_message())


@pytest.mark.asyncio
async def test_disabled_template_raises():
    with patch("src.integrations.senders.whatsapp.settings") as mock_settings:
        mock_settings.WHATSAPP_ENABLED = False
        with pytest.raises(WhatsAppDisabled):
            await send_whatsapp_template("+821000000000")


# ---------- Missing config ----------


@pytest.mark.asyncio
async def test_missing_token_raises():
    with patch("src.integrations.senders.whatsapp.settings") as mock_settings:
        mock_settings.WHATSAPP_ENABLED = True
        mock_settings.WHATSAPP_ACCESS_TOKEN = ""
        mock_settings.WHATSAPP_PHONE_NUMBER_ID = "12345"
        with pytest.raises(WhatsAppSendError, match="ACCESS_TOKEN"):
            await send_whatsapp_template("+821000000000")


@pytest.mark.asyncio
async def test_missing_phone_number_id_raises():
    with patch("src.integrations.senders.whatsapp.settings") as mock_settings:
        mock_settings.WHATSAPP_ENABLED = True
        mock_settings.WHATSAPP_ACCESS_TOKEN = "token123"
        mock_settings.WHATSAPP_PHONE_NUMBER_ID = ""
        with pytest.raises(WhatsAppSendError, match="PHONE_NUMBER_ID"):
            await send_whatsapp_template("+821000000000")


# ---------- Template send ----------


@pytest.mark.asyncio
async def test_template_send_success():
    """Successful template send returns message ID."""
    with patch("src.integrations.senders.whatsapp.settings") as mock_settings:
        mock_settings.WHATSAPP_ENABLED = True
        mock_settings.WHATSAPP_ACCESS_TOKEN = "token123"
        mock_settings.WHATSAPP_PHONE_NUMBER_ID = "999"
        mock_settings.WHATSAPP_TEMPLATE_NAME = "sales_reply_intro"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"messages": [{"id": "wamid.abc123"}]}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await send_whatsapp_template("+821012345678", params=["문의 감사합니다."])

            assert result == "wamid.abc123"
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            body = call_args.kwargs.get("json") or call_args[1].get("json")
            assert body["template"]["name"] == "sales_reply_intro"


# ---------- Invalid recipient ----------


@pytest.mark.asyncio
async def test_invalid_recipient_raises():
    with patch("src.integrations.senders.whatsapp.settings") as mock_settings:
        mock_settings.WHATSAPP_ENABLED = True
        mock_settings.WHATSAPP_ACCESS_TOKEN = "token123"
        mock_settings.WHATSAPP_PHONE_NUMBER_ID = "999"
        mock_settings.WHATSAPP_TEMPLATE_NAME = "test"

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": {"code": 131009, "message": "invalid number"}}
        mock_response.text = "error"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(WhatsAppSendError, match="Invalid WhatsApp recipient"):
                await send_whatsapp_template("+000000000")


# ---------- Freeform send ----------


@pytest.mark.asyncio
async def test_freeform_send_success():
    with patch("src.integrations.senders.whatsapp.settings") as mock_settings:
        mock_settings.WHATSAPP_ENABLED = True
        mock_settings.WHATSAPP_ACCESS_TOKEN = "token123"
        mock_settings.WHATSAPP_PHONE_NUMBER_ID = "999"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"messages": [{"id": "wamid.xyz789"}]}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await send_whatsapp_freeform("+821012345678", "자유 형식 메시지")
            assert result == "wamid.xyz789"


# ---------- send_whatsapp (message-level) ----------


@pytest.mark.asyncio
async def test_send_whatsapp_no_phone_raises():
    """Message without to_address should raise WhatsAppSendError."""
    with patch("src.integrations.senders.whatsapp.settings") as mock_settings:
        mock_settings.WHATSAPP_ENABLED = True
        mock_settings.WHATSAPP_ACCESS_TOKEN = "token123"
        mock_settings.WHATSAPP_PHONE_NUMBER_ID = "999"

        msg = _make_message(to_address="")
        with pytest.raises(WhatsAppSendError, match="phone number"):
            await send_whatsapp(msg)
