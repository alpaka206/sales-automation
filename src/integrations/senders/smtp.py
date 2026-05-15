"""SMTP email sender using stdlib smtplib."""

from __future__ import annotations

import logging
import smtplib
import uuid
from email.mime.text import MIMEText

from ...common.config import settings
from ...db.models import Message

logger = logging.getLogger(__name__)


def _generate_message_id() -> str:
    """Generate a unique Message-ID for SMTP threading."""
    domain = settings.SMTP_FROM_EMAIL.rsplit("@", 1)[-1] if settings.SMTP_FROM_EMAIL else "localhost"
    return f"<{uuid.uuid4()}@{domain}>"


def send_smtp(message: Message) -> None:
    """Send an email via SMTP."""
    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        raise RuntimeError("SMTP credentials not configured.")

    msg = MIMEText(message.body, "plain", "utf-8")
    msg["Subject"] = message.subject or ""
    msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
    msg["To"] = message.to_address or ""

    message_id = _generate_message_id()
    msg["Message-ID"] = message_id

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        server.starttls()
        server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.send_message(msg)

    message.smtp_message_id = message_id

    logger.info("SMTP: sent email to %s, subject=%s, message_id=%s", message.to_address, message.subject, message_id)
