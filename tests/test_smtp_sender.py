"""Tests for SMTP sender."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.integrations.senders.smtp import _generate_message_id, send_smtp


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
    mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

    msg = MagicMock()
    msg.body = "Hello"
    msg.subject = "Test"
    msg.to_address = "to@example.com"
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
    mock_server.login.assert_called_once_with("user", "pass")
    mock_server.send_message.assert_called_once()
    assert msg.smtp_message_id is not None
