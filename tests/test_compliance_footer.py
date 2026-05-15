"""Tests for country/region-specific compliance footer and send-pipeline regression."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.compliance import add_ad_prefix, build_footer


def test_kr_footer():
    footer = build_footer("ko", "user@company.kr", country_code="KR")
    assert "영업 안내" in footer
    assert "수신 거부" in footer
    assert "unsubscribe" in footer


def test_kr_footer_with_registration():
    with patch("src.integrations.compliance.settings") as mock_s:
        mock_s.COMPANY_NAME = "테스트사"
        mock_s.COMPANY_REGISTRATION_NUMBER = "123-45-67890"
        mock_s.COMPANY_ADDRESS = "서울시 강남구"
        mock_s.COMPANY_PRIVACY_POLICY_URL = ""
        mock_s.KOREA_AD_PREFIX_ENABLED = False
        mock_s.SMTP_FROM_EMAIL = "test@test.com"
        mock_s.INTERNAL_API_TOKEN = "test"
        mock_s.APP_HOST = "127.0.0.1"
        mock_s.APP_PORT = 8000
        footer = build_footer("ko", "user@company.kr", country_code="KR")
        assert "123-45-67890" in footer
        assert "서울시 강남구" in footer


def test_us_footer():
    footer = build_footer("en", "user@company.com", country_code="US")
    assert "Unsubscribe" in footer


def test_us_footer_with_address():
    with patch("src.integrations.compliance.settings") as mock_s:
        mock_s.COMPANY_NAME = "TestCo"
        mock_s.COMPANY_REGISTRATION_NUMBER = ""
        mock_s.COMPANY_ADDRESS = "123 Main St, NY"
        mock_s.COMPANY_PRIVACY_POLICY_URL = ""
        mock_s.KOREA_AD_PREFIX_ENABLED = False
        mock_s.INTERNAL_API_TOKEN = "test"
        mock_s.APP_HOST = "127.0.0.1"
        mock_s.APP_PORT = 8000
        footer = build_footer("en", "user@company.com", country_code="US")
        assert "Physical address" in footer
        assert "123 Main St" in footer


def test_eu_footer():
    footer = build_footer("en", "user@company.de", country_code="DE")
    assert "GDPR" in footer
    assert "Unsubscribe" in footer


def test_eu_footer_with_privacy_url():
    with patch("src.integrations.compliance.settings") as mock_s:
        mock_s.COMPANY_NAME = "TestCo"
        mock_s.COMPANY_REGISTRATION_NUMBER = ""
        mock_s.COMPANY_ADDRESS = ""
        mock_s.COMPANY_PRIVACY_POLICY_URL = "https://example.com/privacy"
        mock_s.KOREA_AD_PREFIX_ENABLED = False
        mock_s.INTERNAL_API_TOKEN = "test"
        mock_s.APP_HOST = "127.0.0.1"
        mock_s.APP_PORT = 8000
        footer = build_footer("en", "user@company.de", country_code="DE")
        assert "https://example.com/privacy" in footer


def test_default_footer_ko():
    footer = build_footer("ko", "user@unknown.xyz")
    assert "영업 안내" in footer
    assert "수신 거부" in footer


def test_default_footer_en():
    footer = build_footer("en", "user@unknown.xyz")
    assert "Unsubscribe" in footer


def test_ad_prefix_ko_enabled():
    with patch("src.integrations.compliance.settings") as mock_s:
        mock_s.KOREA_AD_PREFIX_ENABLED = True
        result = add_ad_prefix("가격 안내", "ko")
        assert result == "(광고) 가격 안내"


def test_ad_prefix_ko_disabled():
    with patch("src.integrations.compliance.settings") as mock_s:
        mock_s.KOREA_AD_PREFIX_ENABLED = False
        result = add_ad_prefix("가격 안내", "ko")
        assert result == "가격 안내"


def test_ad_prefix_en():
    with patch("src.integrations.compliance.settings") as mock_s:
        mock_s.KOREA_AD_PREFIX_ENABLED = True
        result = add_ad_prefix("Pricing Info", "en")
        assert result == "[AD] Pricing Info"


def test_ad_prefix_no_double():
    with patch("src.integrations.compliance.settings") as mock_s:
        mock_s.KOREA_AD_PREFIX_ENABLED = True
        result = add_ad_prefix("(광고) 이미 있음", "ko")
        assert result == "(광고) 이미 있음"


# ---------- Regression: outbound message body includes footer after send() ----------


def _make_outbound_message(**overrides) -> MagicMock:
    """Build a mock Message that resembles an outbound agent's output."""
    msg = MagicMock()
    msg.id = overrides.get("id", 1)
    msg.channel = overrides.get("channel", "email")
    msg.direction = overrides.get("direction", "outbound")
    msg.to_address = overrides.get("to_address", "buyer@company.kr")
    msg.subject = overrides.get("subject", "가격 안내")
    msg.body = overrides.get("body", "안녕하세요, 가격 안내드립니다.")
    msg.language = overrides.get("language", "ko")
    msg.conversation = MagicMock()
    msg.conversation.contact_id = 100
    return msg


@pytest.mark.asyncio
@patch("src.integrations.senders.send_smtp")
@patch("src.integrations.compliance.is_suppressed", return_value=False)
async def test_outbound_message_gets_unsubscribe_footer(mock_suppressed, mock_smtp):
    """Regression: send() must append unsubscribe link to outbound email body."""
    from src.integrations.senders import send

    msg = _make_outbound_message()
    original_body = msg.body

    with patch("src.integrations.senders.settings") as s:
        s.EMAIL_PROVIDER = "smtp"
        s.WHATSAPP_ENABLED = False
        await send(msg)

    assert msg.body != original_body
    assert "unsubscribe" in msg.body.lower()


@pytest.mark.asyncio
@patch("src.integrations.senders.send_smtp")
@patch("src.integrations.compliance.is_suppressed", return_value=False)
async def test_outbound_message_gets_sender_info_footer(mock_suppressed, mock_smtp):
    """Regression: send() must append sender info (company name) to outbound email body."""
    from src.integrations.senders import send

    msg = _make_outbound_message()

    with patch("src.integrations.senders.settings") as s:
        s.EMAIL_PROVIDER = "smtp"
        s.WHATSAPP_ENABLED = False
        await send(msg)

    assert "---" in msg.body
    assert "영업 안내" in msg.body or "sales outreach" in msg.body.lower()


@pytest.mark.asyncio
@patch("src.integrations.senders.send_smtp")
@patch("src.integrations.compliance.is_suppressed", return_value=False)
async def test_inbound_message_no_footer(mock_suppressed, mock_smtp):
    """Inbound messages must not get a compliance footer appended."""
    from src.integrations.senders import send

    msg = _make_outbound_message(direction="inbound")
    original_body = msg.body

    with patch("src.integrations.senders.settings") as s:
        s.EMAIL_PROVIDER = "smtp"
        s.WHATSAPP_ENABLED = False
        await send(msg)

    assert msg.body == original_body
