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
