"""Tests for Slack integration with mocked HTTP."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from src.integrations.slack import SlackNotConfigured, post_approval_card, post_message


# ---------- post_approval_card ----------


def test_post_approval_card_not_configured() -> None:
    with patch("src.integrations.slack.settings") as s:
        s.SLACK_BOT_TOKEN = ""
        s.SLACK_APPROVAL_CHANNEL_ID = ""
        with pytest.raises(SlackNotConfigured):
            post_approval_card(1, "Sub", "Body", 80, "inquiry", "email")


@respx.mock
def test_post_approval_card_success() -> None:
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1"})
    )
    with patch("src.integrations.slack.settings") as s:
        s.SLACK_BOT_TOKEN = "xoxb-test"
        s.SLACK_APPROVAL_CHANNEL_ID = "C123"
        post_approval_card(42, "Hello", "Body snippet", 90, "purchase_inquiry", "email")


@respx.mock
def test_post_approval_card_api_error_logged() -> None:
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )
    with patch("src.integrations.slack.settings") as s:
        s.SLACK_BOT_TOKEN = "xoxb-test"
        s.SLACK_APPROVAL_CHANNEL_ID = "C999"
        post_approval_card(1, "Sub", "Body", None, "general", "email")


@respx.mock
def test_post_approval_card_enriched_korean_card() -> None:
    """Inbound card surfaces who/what-asked/what-we-send + a deep link in Korean."""
    import json as _json

    route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1"})
    )
    with patch("src.integrations.slack.settings") as s:
        s.SLACK_BOT_TOKEN = "xoxb-test"
        s.SLACK_APPROVAL_CHANNEL_ID = "C123"
        s.PUBLIC_BASE_URL = "https://sales.example.com"
        post_approval_card(
            42, "Re: 견적", "안녕하세요, 답변 초안입니다.", 78, "purchase_inquiry", "email",
            title="새 인바운드 문의 — 회신 검토 요청",
            inquiry="Hello, what is your pricing?",
            contact_name="Tanaka Yuki",
            contact_company="SaaS Japan Inc.",
            contact_email="tanaka@saas.jp",
        )

    payload = _json.loads(route.calls.last.request.content)
    blob = _json.dumps(payload, ensure_ascii=False)
    # who / inquiry / draft / deep link all present
    assert "새 인바운드 문의" in blob
    assert "Tanaka Yuki" in blob and "tanaka@saas.jp" in blob
    assert "Hello, what is your pricing?" in blob
    assert "안녕하세요, 답변 초안입니다." in blob
    assert "https://sales.example.com/messages/42" in blob


@respx.mock
def test_post_approval_card_outbound_omits_inquiry() -> None:
    """Outbound cold mail has no inbound inquiry → no 문의 내용 section."""
    import json as _json

    route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1"})
    )
    with patch("src.integrations.slack.settings") as s:
        s.SLACK_BOT_TOKEN = "xoxb-test"
        s.SLACK_APPROVAL_CHANNEL_ID = "C123"
        s.PUBLIC_BASE_URL = "https://sales.example.com"
        post_approval_card(
            7, "신규 협업 제안", "콜드메일 본문", 80, "outbound_opening", "email",
            title="아웃바운드 신규 메일 — 검토 요청",
        )

    blob = _json.dumps(_json.loads(route.calls.last.request.content), ensure_ascii=False)
    assert "문의 내용" not in blob
    assert "아웃바운드 신규 메일" in blob


@respx.mock
def test_post_approval_card_http_error() -> None:
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(500, json={})
    )
    with patch("src.integrations.slack.settings") as s:
        s.SLACK_BOT_TOKEN = "xoxb-test"
        s.SLACK_APPROVAL_CHANNEL_ID = "C123"
        with pytest.raises(httpx.HTTPStatusError):
            post_approval_card(1, "Sub", "Body", 50, "support", "email")


# ---------- post_message ----------


def test_post_message_not_configured() -> None:
    with patch("src.integrations.slack.settings") as s:
        s.SLACK_BOT_TOKEN = ""
        with pytest.raises(SlackNotConfigured):
            post_message("C123", "hello")


@respx.mock
def test_post_message_success() -> None:
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    with patch("src.integrations.slack.settings") as s:
        s.SLACK_BOT_TOKEN = "xoxb-test"
        post_message("C123", "Report text")


@respx.mock
def test_post_message_api_error() -> None:
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
    )
    with patch("src.integrations.slack.settings") as s:
        s.SLACK_BOT_TOKEN = "xoxb-test"
        post_message("C123", "text")
