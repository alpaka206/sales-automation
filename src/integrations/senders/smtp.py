"""SMTP email sender using stdlib smtplib."""

from __future__ import annotations

import logging
import smtplib
import uuid
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import getaddresses

from ...common.config import settings
from ...db.models import Message

logger = logging.getLogger(__name__)


class SMTPHeaderInjectionError(ValueError):
    """Raised when a header value contains CR/LF — refuses to send."""


def _reject_crlf(field: str, value: str) -> None:
    """Defense against email-header-injection (To/Subject containing CRLF)."""
    if value and ("\r" in value or "\n" in value):
        raise SMTPHeaderInjectionError(f"{field} contains illegal CR/LF characters")


def _generate_message_id() -> str:
    """Generate a unique Message-ID for SMTP threading."""
    domain = settings.SMTP_FROM_EMAIL.rsplit("@", 1)[-1] if settings.SMTP_FROM_EMAIL else "localhost"
    return f"<{uuid.uuid4()}@{domain}>"


def _build_message(message: Message) -> EmailMessage:
    """Build an RFC-compliant EmailMessage. EmailMessage handles RFC 2047 encoding of
    non-ASCII headers automatically (unlike legacy MIMEText, which we used before)."""
    _reject_crlf("Subject", message.subject or "")
    _reject_crlf("To", message.to_address or "")
    _reject_crlf("From-Name", settings.SMTP_FROM_NAME or "")
    _reject_crlf("From-Email", settings.SMTP_FROM_EMAIL or "")

    msg = EmailMessage()
    msg.set_content(message.body or "", subtype="plain", charset="utf-8")
    msg["Subject"] = message.subject or ""

    # Use structured Address so the display name is RFC-2047 encoded if it contains
    # non-ASCII (e.g. 한국어 sender names).
    from_local, _, from_domain = (settings.SMTP_FROM_EMAIL or "").partition("@")
    if from_local and from_domain:
        msg["From"] = Address(
            display_name=settings.SMTP_FROM_NAME or "",
            username=from_local,
            domain=from_domain,
        )

    if message.to_address:
        to_list = getaddresses([message.to_address])
        # getaddresses returns [(name, email), ...]; we reuse as-is — it's already
        # tolerant of comma-separated lists.
        msg["To"] = ", ".join(addr for _name, addr in to_list if addr) or message.to_address

    # Threading: bind reply to the inbound message we're replying to.
    if message.in_reply_to:
        _reject_crlf("In-Reply-To", message.in_reply_to)
        msg["In-Reply-To"] = message.in_reply_to
        msg["References"] = message.in_reply_to

    message_id = _generate_message_id()
    msg["Message-ID"] = message_id
    return msg


_TRANSIENT_SMTP_CODES = {421, 450, 451, 452, 471}


class SMTPPermanentError(RuntimeError):
    """Bad recipient, auth, malformed — retry won't help."""


class SMTPTransientError(RuntimeError):
    """Connection / temp failure — retry is worth trying."""


def send_smtp(message: Message) -> None:
    """Send an email via SMTP.

    Classifies SMTP failures into transient vs permanent so the worker can decide
    whether to retry. Header CRLF injection is rejected before connecting.
    """
    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        raise SMTPPermanentError("SMTP credentials not configured.")

    msg = _build_message(message)
    message_id = msg["Message-ID"]

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise SMTPPermanentError(f"SMTP auth failed: {exc}") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise SMTPPermanentError(f"All recipients refused: {exc.recipients}") from exc
    except smtplib.SMTPResponseException as exc:
        if exc.smtp_code in _TRANSIENT_SMTP_CODES:
            raise SMTPTransientError(f"transient SMTP error {exc.smtp_code}: {exc.smtp_error}") from exc
        raise SMTPPermanentError(f"permanent SMTP error {exc.smtp_code}: {exc.smtp_error}") from exc
    except (TimeoutError, ConnectionError, OSError) as exc:
        raise SMTPTransientError(f"SMTP connection failure: {exc}") from exc

    message.smtp_message_id = message_id

    logger.info(
        "SMTP: sent email to %s, subject=%s, message_id=%s",
        message.to_address,
        message.subject,
        message_id,
    )
