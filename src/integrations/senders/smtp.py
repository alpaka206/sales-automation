"""SMTP email sender using stdlib smtplib."""

from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

from ...common.config import settings
from ...db.models import Message

logger = logging.getLogger(__name__)


def send_smtp(message: Message) -> None:
    """Send an email via SMTP."""
    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        raise RuntimeError("SMTP credentials not configured.")

    msg = MIMEText(message.body, "plain", "utf-8")
    msg["Subject"] = message.subject or ""
    msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
    msg["To"] = message.to_address or ""

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        server.starttls()
        server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.send_message(msg)

    logger.info("SMTP: sent email to %s, subject=%s", message.to_address, message.subject)
