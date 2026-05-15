"""Tests for IMAP reply matching logic in reply_check."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.agents.reply_check import _matches_reply


def _make_outbound(
    smtp_message_id: str | None = None,
    to_address: str | None = None,
    sent_at: datetime | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.smtp_message_id = smtp_message_id
    msg.to_address = to_address
    msg.sent_at = sent_at
    return msg


def test_matches_by_in_reply_to() -> None:
    outbound = _make_outbound(smtp_message_id="<abc@myapp.com>")
    imap_msg = {
        "in_reply_to": "<abc@myapp.com>",
        "references": "",
        "from_addr": "someone@other.com",
        "received_at": None,
    }
    assert _matches_reply(outbound, imap_msg) is True


def test_matches_by_references() -> None:
    outbound = _make_outbound(smtp_message_id="<def@myapp.com>")
    imap_msg = {
        "in_reply_to": "",
        "references": "<original@x.com> <def@myapp.com>",
        "from_addr": "someone@other.com",
        "received_at": None,
    }
    assert _matches_reply(outbound, imap_msg) is True


def test_no_match_different_message_id() -> None:
    outbound = _make_outbound(smtp_message_id="<abc@myapp.com>")
    imap_msg = {
        "in_reply_to": "<xyz@other.com>",
        "references": "",
        "from_addr": "someone@other.com",
        "received_at": None,
    }
    assert _matches_reply(outbound, imap_msg) is False


def test_fallback_from_address_match() -> None:
    sent_time = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    outbound = _make_outbound(
        smtp_message_id=None,
        to_address="client@corp.kr",
        sent_at=sent_time,
    )
    imap_msg = {
        "in_reply_to": "",
        "references": "",
        "from_addr": "client@corp.kr",
        "received_at": datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
    }
    assert _matches_reply(outbound, imap_msg) is True


def test_fallback_no_match_before_sent() -> None:
    sent_time = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    outbound = _make_outbound(
        smtp_message_id=None,
        to_address="client@corp.kr",
        sent_at=sent_time,
    )
    imap_msg = {
        "in_reply_to": "",
        "references": "",
        "from_addr": "client@corp.kr",
        "received_at": datetime(2026, 5, 17, 8, 0, tzinfo=timezone.utc),
    }
    assert _matches_reply(outbound, imap_msg) is False


def test_fallback_no_match_different_sender() -> None:
    sent_time = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    outbound = _make_outbound(
        smtp_message_id=None,
        to_address="client@corp.kr",
        sent_at=sent_time,
    )
    imap_msg = {
        "in_reply_to": "",
        "references": "",
        "from_addr": "stranger@other.com",
        "received_at": datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
    }
    assert _matches_reply(outbound, imap_msg) is False
