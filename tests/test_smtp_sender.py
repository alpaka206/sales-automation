"""Tests for SMTP sender."""

from __future__ import annotations

import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.senders.smtp import SMTPDeliveryUnknown, _generate_message_id, send_smtp


def test_generate_message_id_with_domain() -> None:
    with patch("src.integrations.senders.smtp.settings") as mock_settings:
        mock_settings.SMTP_FROM_EMAIL = "user@example.com"
        mid = _generate_message_id()
        assert mid.startswith("<")
        assert mid.endswith("@example.com>")


def test_generate_message_id_no_email() -> None:
    with patch("src.integrations.senders.smtp.settings") as mock_settings:
        mock_settings.SMTP_FROM_EMAIL = ""
        mid = _generate_message_id()
        assert "@localhost>" in mid


def test_send_smtp_no_credentials() -> None:
    msg = MagicMock()
    with patch("src.integrations.senders.smtp.settings") as mock_settings:
        mock_settings.SMTP_USERNAME = ""
        mock_settings.SMTP_PASSWORD = ""
        with pytest.raises(RuntimeError, match="SMTP credentials"):
            send_smtp(msg)


@patch("src.integrations.senders.smtp.smtplib.SMTP")
def test_send_smtp_success(mock_smtp_cls) -> None:
    mock_server = MagicMock()
    mock_smtp_cls.return_value = mock_server

    msg = MagicMock()
    msg.body = "Hello"
    msg.subject = "Test"
    msg.to_address = "to@example.com"
    msg.in_reply_to = None  # don't set the threading header on this test message
    msg.smtp_message_id = None

    with patch("src.integrations.senders.smtp.settings") as mock_settings:
        mock_settings.SMTP_USERNAME = "user"
        mock_settings.SMTP_PASSWORD = "pass"
        mock_settings.SMTP_HOST = "smtp.gmail.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_FROM_EMAIL = "from@example.com"
        mock_settings.SMTP_FROM_NAME = "Sender"
        send_smtp(msg)

    mock_server.starttls.assert_called_once()
    tls_context = mock_server.starttls.call_args.kwargs["context"]
    assert tls_context.verify_mode == ssl.CERT_REQUIRED
    assert tls_context.check_hostname is True
    mock_server.login.assert_called_once_with("user", "pass")
    mock_server.send_message.assert_called_once()
    assert msg.smtp_message_id is not None


@patch("src.integrations.senders.smtp.smtplib.SMTP")
def test_disconnect_during_data_is_not_safe_to_retry(mock_smtp_cls) -> None:
    server = MagicMock()
    server.send_message.side_effect = OSError("connection lost after DATA")
    mock_smtp_cls.return_value = server
    msg = MagicMock(
        body="Hello",
        subject="Test",
        to_address="to@example.com",
        in_reply_to=None,
        smtp_message_id=None,
    )

    with patch("src.integrations.senders.smtp.settings") as mock_settings:
        mock_settings.SMTP_USERNAME = "user"
        mock_settings.SMTP_PASSWORD = "pass"
        mock_settings.SMTP_HOST = "smtp.example.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_FROM_EMAIL = "from@example.com"
        mock_settings.SMTP_FROM_NAME = "Sender"
        with pytest.raises(SMTPDeliveryUnknown):
            send_smtp(msg)


def test_message_id_is_stable_for_same_database_row() -> None:
    with patch("src.integrations.senders.smtp.settings") as mock_settings:
        mock_settings.SMTP_FROM_EMAIL = "from@example.com"
        assert _generate_message_id(42) == _generate_message_id(42)
        assert _generate_message_id(42) != _generate_message_id(43)


@pytest.mark.asyncio
async def test_async_sender_runs_smtp_off_event_loop() -> None:
    from src.integrations.senders import send
    from src.integrations.senders.smtp import send_smtp as smtp_callable

    msg = MagicMock()
    msg.id = 42
    msg.channel = "email"
    msg.direction = "incoming"
    msg.conversation.contact = None

    with patch(
        "src.integrations.senders.asyncio.to_thread", new_callable=AsyncMock
    ) as mock_to_thread:
        await send(msg)

    mock_to_thread.assert_awaited_once_with(smtp_callable, msg)


def test_send_smtp_rejects_crlf_in_subject() -> None:
    """Header-injection guard: \\r\\n in Subject must raise before connecting."""
    from src.integrations.senders.smtp import SMTPHeaderInjectionError

    msg = MagicMock()
    msg.body = "Hello"
    msg.subject = "OK\r\nBcc: attacker@evil.com"
    msg.to_address = "to@example.com"
    msg.in_reply_to = None

    with patch("src.integrations.senders.smtp.settings") as mock_settings:
        mock_settings.SMTP_USERNAME = "user"
        mock_settings.SMTP_PASSWORD = "pass"
        mock_settings.SMTP_FROM_EMAIL = "from@example.com"
        mock_settings.SMTP_FROM_NAME = "Sender"
        with pytest.raises(SMTPHeaderInjectionError):
            send_smtp(msg)


def test_send_smtp_rejects_crlf_in_to() -> None:
    from src.integrations.senders.smtp import SMTPHeaderInjectionError

    msg = MagicMock()
    msg.body = "Hello"
    msg.subject = "Hi"
    msg.to_address = "to@example.com\r\nBcc: attacker@evil.com"
    msg.in_reply_to = None

    with patch("src.integrations.senders.smtp.settings") as mock_settings:
        mock_settings.SMTP_USERNAME = "user"
        mock_settings.SMTP_PASSWORD = "pass"
        mock_settings.SMTP_FROM_EMAIL = "from@example.com"
        mock_settings.SMTP_FROM_NAME = "Sender"
        with pytest.raises(SMTPHeaderInjectionError):
            send_smtp(msg)


def test_send_smtp_threads_via_in_reply_to() -> None:
    """When the outgoing reply has in_reply_to set, In-Reply-To + References are set."""
    msg = MagicMock()
    msg.body = "Reply body"
    msg.subject = "Re: Hi"
    msg.to_address = "to@example.com"
    msg.in_reply_to = "<orig-123@example.com>"
    msg.smtp_message_id = None

    with patch("src.integrations.senders.smtp.smtplib.SMTP") as mock_smtp_cls, patch(
        "src.integrations.senders.smtp.settings"
    ) as mock_settings:
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server
        mock_settings.SMTP_USERNAME = "user"
        mock_settings.SMTP_PASSWORD = "pass"
        mock_settings.SMTP_HOST = "smtp.gmail.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_FROM_EMAIL = "from@example.com"
        mock_settings.SMTP_FROM_NAME = "Sender"

        send_smtp(msg)

        sent = mock_server.send_message.call_args[0][0]
        assert sent["In-Reply-To"] == "<orig-123@example.com>"
        assert sent["References"] == "<orig-123@example.com>"
