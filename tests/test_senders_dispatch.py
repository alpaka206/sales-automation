"""Tests for senders dispatch — suppression, whatsapp channel, hubspot fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.senders import send


def _make_message(**overrides) -> MagicMock:
    msg = MagicMock()
    msg.id = overrides.get("id", 1)
    msg.channel = overrides.get("channel", "email")
    msg.direction = overrides.get("direction", "outbound")
    msg.to_address = overrides.get("to_address", "to@example.com")
    msg.subject = overrides.get("subject", "Test")
    msg.body = overrides.get("body", "Hello")
    msg.language = overrides.get("language", "ko")
    msg.conversation = MagicMock()
    msg.conversation.contact_id = overrides.get("contact_id", 100)
    return msg


# ---------- WhatsApp channel direct send (covers line 72-74) ----------


@pytest.mark.asyncio
@patch("src.integrations.senders.send_whatsapp", new_callable=AsyncMock)
async def test_whatsapp_channel_sends_directly(mock_wa) -> None:
    msg = _make_message(channel="whatsapp")
    await send(msg)
    mock_wa.assert_called_once_with(msg)


# ---------- Suppression (covers lines 78-86) ----------


@pytest.mark.asyncio
@patch("src.integrations.senders.send_smtp")
@patch("src.integrations.compliance.is_suppressed", return_value=True)
@patch("src.integrations.compliance.append_footer", side_effect=lambda b, *a: b)
@patch("src.db.session.SessionLocal")
async def test_suppressed_address_skips_send(mock_session_cls, mock_footer, mock_suppressed, mock_smtp) -> None:
    msg = _make_message(to_address="blocked@example.com")

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.get.return_value = msg
    mock_session_cls.return_value = mock_session

    with patch("src.integrations.senders.settings") as s:
        s.EMAIL_PROVIDER = "smtp"
        s.WHATSAPP_ENABLED = False
        await send(msg)

    mock_smtp.assert_not_called()


# ---------- HubSpot provider path (covers lines 94-108) ----------


@pytest.mark.asyncio
@patch("src.integrations.senders.send_smtp")
@patch("src.integrations.compliance.is_suppressed", return_value=False)
@patch("src.integrations.compliance.append_footer", side_effect=lambda b, *a: b)
async def test_hubspot_provider_send(mock_footer, mock_suppressed, mock_smtp) -> None:
    msg = _make_message()

    with patch("src.integrations.senders.settings") as s, \
         patch("src.integrations.hubspot.HubSpotClient") as MockHSClient:
        s.EMAIL_PROVIDER = "hubspot"
        s.WHATSAPP_ENABLED = False
        s.SMTP_FROM_EMAIL = "from@x.com"

        hs_instance = AsyncMock()
        hs_instance.send_email = AsyncMock(return_value="eng-1")
        MockHSClient.return_value = hs_instance

        await send(msg)

    mock_smtp.assert_not_called()
    hs_instance.send_email.assert_called_once()
    hs_instance.close.assert_called_once()


@pytest.mark.asyncio
@patch("src.integrations.senders.send_smtp")
@patch("src.integrations.compliance.is_suppressed", return_value=False)
@patch("src.integrations.compliance.append_footer", side_effect=lambda b, *a: b)
async def test_hubspot_not_configured_falls_back_to_smtp(mock_footer, mock_suppressed, mock_smtp) -> None:
    from src.integrations.hubspot import HubSpotNotConfigured

    msg = _make_message()

    with patch("src.integrations.senders.settings") as s, \
         patch("src.integrations.hubspot.HubSpotClient") as MockHSClient:
        s.EMAIL_PROVIDER = "hubspot"
        s.WHATSAPP_ENABLED = False
        s.SMTP_FROM_EMAIL = "from@x.com"

        MockHSClient.side_effect = HubSpotNotConfigured("no token")

        await send(msg)

    mock_smtp.assert_called_once_with(msg)


# ---------- Unknown provider (covers line 109-110) ----------


@pytest.mark.asyncio
@patch("src.integrations.compliance.is_suppressed", return_value=False)
@patch("src.integrations.compliance.append_footer", side_effect=lambda b, *a: b)
async def test_unknown_provider_raises(mock_footer, mock_suppressed) -> None:
    msg = _make_message()

    with patch("src.integrations.senders.settings") as s:
        s.EMAIL_PROVIDER = "carrier_pigeon"
        s.WHATSAPP_ENABLED = False

        with pytest.raises(ValueError, match="Unknown EMAIL_PROVIDER"):
            await send(msg)
