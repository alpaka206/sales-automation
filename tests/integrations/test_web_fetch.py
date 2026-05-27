"""Tests for src/integrations/web_fetch.py — homepage metadata extraction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.integrations.web_fetch import (
    _extract_meta,
    _is_ssrf_target,
    fetch_homepage_meta,
)


class TestSSRFGuard:
    def test_localhost_blocked(self):
        assert _is_ssrf_target("localhost") is True

    def test_loopback_blocked(self):
        assert _is_ssrf_target("127.0.0.1") is True

    def test_metadata_endpoint_blocked(self):
        assert _is_ssrf_target("169.254.169.254") is True

    def test_ipv6_loopback_blocked(self):
        assert _is_ssrf_target("[::1]") is True

    def test_normal_domain_not_blocked(self):
        with patch("src.integrations.web_fetch._is_private_ip", return_value=False):
            assert _is_ssrf_target("example.com") is False


class TestExtractMeta:
    def test_extracts_title_and_description(self):
        html = """
        <html>
        <head>
            <title>Acme Corp - Leading SaaS Platform</title>
            <meta name="description" content="Acme builds enterprise tools.">
            <meta property="og:description" content="OG desc here.">
            <meta name="keywords" content="saas,enterprise,tools">
        </head>
        </html>
        """
        meta = _extract_meta(html)
        assert meta["title"] == "Acme Corp - Leading SaaS Platform"
        assert meta["description"] == "Acme builds enterprise tools."
        assert meta["og_description"] == "OG desc here."
        assert meta["keywords"] == "saas,enterprise,tools"

    def test_handles_content_before_name(self):
        html = '<meta content="reversed" name="description">'
        meta = _extract_meta(html)
        assert meta["description"] == "reversed"

    def test_handles_missing_tags(self):
        html = "<html><body>No meta</body></html>"
        meta = _extract_meta(html)
        assert meta == {}


class TestFetchHomepageMeta:
    def test_ssrf_blocked_returns_blocked_status(self):
        result = fetch_homepage_meta("127.0.0.1")
        assert result.status == "blocked"

    def test_localhost_returns_blocked(self):
        result = fetch_homepage_meta("localhost")
        assert result.status == "blocked"

    def test_metadata_ip_returns_blocked(self):
        result = fetch_homepage_meta("169.254.169.254")
        assert result.status == "blocked"

    @patch("src.integrations.web_fetch._is_ssrf_target", return_value=False)
    def test_successful_html_fetch(self, _mock_ssrf):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.text = """
        <html><head>
            <title>Test Corp</title>
            <meta name="description" content="We make things.">
        </head></html>
        """

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with patch("src.integrations.web_fetch.httpx.Client", return_value=mock_client):
            result = fetch_homepage_meta("test.com", timeout=5.0)

        assert result.status == "ok"
        assert result.title == "Test Corp"
        assert result.description == "We make things."

    @patch("src.integrations.web_fetch._is_ssrf_target", return_value=False)
    def test_non_html_returns_blocked(self, _mock_ssrf):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.text = '{"api": true}'

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with patch("src.integrations.web_fetch.httpx.Client", return_value=mock_client):
            result = fetch_homepage_meta("api.example.com", timeout=5.0)

        assert result.status == "blocked"

    @patch("src.integrations.web_fetch._is_ssrf_target", return_value=False)
    def test_timeout_returns_timeout_status(self, _mock_ssrf):
        import httpx

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.TimeoutException("timed out")

        with patch("src.integrations.web_fetch.httpx.Client", return_value=mock_client):
            result = fetch_homepage_meta("slow.example.com", timeout=1.0)

        assert result.status == "timeout"

    @patch("src.integrations.web_fetch._is_ssrf_target", return_value=False)
    def test_http_5xx_returns_5xx_status(self, _mock_ssrf):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.text = "<html><body>503</body></html>"

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with patch("src.integrations.web_fetch.httpx.Client", return_value=mock_client):
            result = fetch_homepage_meta("down.example.com", timeout=5.0)

        assert result.status == "http_5xx"
