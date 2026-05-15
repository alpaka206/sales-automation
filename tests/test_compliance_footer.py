"""Tests for country/region-specific compliance footer."""

from __future__ import annotations

from unittest.mock import patch

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
