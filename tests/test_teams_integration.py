"""Tests for Teams integration with mocked HTTP."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from src.integrations.teams import TeamsNotConfigured, post_approval_card, post_message

WEBHOOK_URL = "https://outlook.office.com/webhook/test"


# ---------- post_approval_card ----------


def test_post_approval_card_not_configured() -> None:
    with patch("src.integrations.teams.settings") as s:
        s.TEAMS_WEBHOOK_URL = ""
        with pytest.raises(TeamsNotConfigured):
            post_approval_card(1, "Sub", "Body", 80, "inquiry", "email")


@respx.mock
def test_post_approval_card_success() -> None:
    respx.post(WEBHOOK_URL).mock(
        return_value=httpx.Response(200, text="1")
    )
    with patch("src.integrations.teams.settings") as s:
        s.TEAMS_WEBHOOK_URL = WEBHOOK_URL
        post_approval_card(42, "Hello", "Body snippet", 90, "purchase_inquiry", "email")


@respx.mock
def test_post_approval_card_with_none_score() -> None:
    respx.post(WEBHOOK_URL).mock(
        return_value=httpx.Response(200, text="1")
    )
    with patch("src.integrations.teams.settings") as s:
        s.TEAMS_WEBHOOK_URL = WEBHOOK_URL
        post_approval_card(1, "Sub", "Body", None, "general", "email")


@respx.mock
def test_post_approval_card_http_error() -> None:
    respx.post(WEBHOOK_URL).mock(
        return_value=httpx.Response(500, text="error")
    )
    with patch("src.integrations.teams.settings") as s:
        s.TEAMS_WEBHOOK_URL = WEBHOOK_URL
        with pytest.raises(httpx.HTTPStatusError):
            post_approval_card(1, "Sub", "Body", 50, "support", "email")


# ---------- post_message ----------


def test_post_message_not_configured() -> None:
    with patch("src.integrations.teams.settings") as s:
        s.TEAMS_WEBHOOK_URL = ""
        with pytest.raises(TeamsNotConfigured):
            post_message("hello")


@respx.mock
def test_post_message_success() -> None:
    respx.post(WEBHOOK_URL).mock(
        return_value=httpx.Response(200, text="1")
    )
    with patch("src.integrations.teams.settings") as s:
        s.TEAMS_WEBHOOK_URL = WEBHOOK_URL
        post_message("Report text")


@respx.mock
def test_post_message_http_error() -> None:
    respx.post(WEBHOOK_URL).mock(
        return_value=httpx.Response(500, text="error")
    )
    with patch("src.integrations.teams.settings") as s:
        s.TEAMS_WEBHOOK_URL = WEBHOOK_URL
        with pytest.raises(httpx.HTTPStatusError):
            post_message("text")
