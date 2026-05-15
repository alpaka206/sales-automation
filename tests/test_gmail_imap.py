"""Tests for Gmail IMAP client with mocked imaplib."""

from __future__ import annotations

import email.mime.text
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.integrations.gmail_imap import (
    IMAPClient,
    IMAPNotConfigured,
    _decode_subject,
    _extract_email_addr,
    _parse_date,
)


def test_extract_email_addr() -> None:
    assert _extract_email_addr("Kim <kim@example.kr>") == "kim@example.kr"
    assert _extract_email_addr("plain@test.com") == "plain@test.com"
    assert _extract_email_addr('"Name" <UPPER@CASE.COM>') == "upper@case.com"


def test_decode_subject_plain() -> None:
    assert _decode_subject("Hello World") == "Hello World"


def test_decode_subject_encoded() -> None:
    encoded = "=?utf-8?B?7YWM7Iqk7Yq4?="
    result = _decode_subject(encoded)
    assert result == "테스트"


def test_parse_date() -> None:
    dt = _parse_date("Mon, 19 May 2026 09:30:00 +0900")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.hour == 0
    assert dt.minute == 30


def test_parse_date_invalid() -> None:
    assert _parse_date("not a date") is None


def test_imap_not_configured() -> None:
    with patch("src.integrations.gmail_imap.settings") as mock_settings:
        mock_settings.GMAIL_IMAP_USERNAME = ""
        mock_settings.GMAIL_IMAP_PASSWORD = ""
        mock_settings.GMAIL_IMAP_FOLDER = "INBOX"
        with pytest.raises(IMAPNotConfigured):
            IMAPClient()


def _build_raw_email(
    from_addr: str = "sender@test.com",
    subject: str = "Re: Test",
    body: str = "Thanks for reaching out.",
    message_id: str = "<reply@test.com>",
    in_reply_to: str = "<original@test.com>",
    date: str = "Mon, 19 May 2026 10:00:00 +0000",
) -> bytes:
    """Build a raw RFC822 email for testing."""
    msg = email.mime.text.MIMEText(body, "plain", "utf-8")
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["In-Reply-To"] = in_reply_to
    msg["Date"] = date
    return msg.as_bytes()


def test_fetch_replies_basic() -> None:
    raw = _build_raw_email()

    mock_conn = MagicMock()
    mock_conn.search.return_value = ("OK", [b"1"])
    mock_conn.fetch.return_value = ("OK", [(b"1", raw)])

    with (
        patch("src.integrations.gmail_imap.imaplib.IMAP4_SSL", return_value=mock_conn),
        patch("src.integrations.gmail_imap.settings") as mock_settings,
    ):
        mock_settings.GMAIL_IMAP_USERNAME = "test@gmail.com"
        mock_settings.GMAIL_IMAP_PASSWORD = "app-password"
        mock_settings.GMAIL_IMAP_FOLDER = "INBOX"

        client = IMAPClient("test@gmail.com", "app-password")
        replies = client.fetch_replies(since_dt=datetime(2026, 5, 18, tzinfo=timezone.utc))

    assert len(replies) == 1
    assert replies[0]["from_addr"] == "sender@test.com"
    assert replies[0]["in_reply_to"] == "<original@test.com>"
    assert replies[0]["subject"] == "Re: Test"
    assert "Thanks" in replies[0]["body_snippet"]


def test_fetch_replies_empty_inbox() -> None:
    mock_conn = MagicMock()
    mock_conn.search.return_value = ("OK", [b""])

    with (
        patch("src.integrations.gmail_imap.imaplib.IMAP4_SSL", return_value=mock_conn),
        patch("src.integrations.gmail_imap.settings") as mock_settings,
    ):
        mock_settings.GMAIL_IMAP_USERNAME = "test@gmail.com"
        mock_settings.GMAIL_IMAP_PASSWORD = "app-password"
        mock_settings.GMAIL_IMAP_FOLDER = "INBOX"

        client = IMAPClient("test@gmail.com", "app-password")
        replies = client.fetch_replies(since_dt=datetime(2026, 5, 18, tzinfo=timezone.utc))

    assert len(replies) == 0


def test_fetch_replies_multiple() -> None:
    raw1 = _build_raw_email(from_addr="a@test.com", subject="Re: First")
    raw2 = _build_raw_email(from_addr="b@test.com", subject="Re: Second")

    mock_conn = MagicMock()
    mock_conn.search.return_value = ("OK", [b"1 2"])
    mock_conn.fetch.side_effect = [
        ("OK", [(b"1", raw1)]),
        ("OK", [(b"2", raw2)]),
    ]

    with (
        patch("src.integrations.gmail_imap.imaplib.IMAP4_SSL", return_value=mock_conn),
        patch("src.integrations.gmail_imap.settings") as mock_settings,
    ):
        mock_settings.GMAIL_IMAP_USERNAME = "test@gmail.com"
        mock_settings.GMAIL_IMAP_PASSWORD = "app-password"
        mock_settings.GMAIL_IMAP_FOLDER = "INBOX"

        client = IMAPClient("test@gmail.com", "app-password")
        replies = client.fetch_replies(since_dt=datetime(2026, 5, 18, tzinfo=timezone.utc))

    assert len(replies) == 2
    assert replies[0]["from_addr"] == "a@test.com"
    assert replies[1]["from_addr"] == "b@test.com"
