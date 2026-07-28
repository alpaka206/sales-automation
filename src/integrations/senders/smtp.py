"""SMTP email sender using stdlib smtplib."""

from __future__ import annotations

import copy
import logging
import smtplib
import ssl
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


def _generate_message_id(message_key: str | int | None = None) -> str:
    """Generate a stable per-row Message-ID, or a random one without a row key."""
    domain = (
        settings.SMTP_FROM_EMAIL.rsplit("@", 1)[-1] if settings.SMTP_FROM_EMAIL else "localhost"
    )
    unique = (
        uuid.uuid5(uuid.NAMESPACE_URL, f"sales-automation:{domain}:{message_key}")
        if message_key is not None
        else uuid.uuid4()
    )
    return f"<{unique}@{domain}>"


def _build_message(message: Message) -> EmailMessage:
    """Build an RFC-compliant EmailMessage. EmailMessage handles RFC 2047 encoding of
    non-ASCII headers automatically (unlike legacy MIMEText, which we used before)."""
    _reject_crlf("Subject", message.subject or "")
    _reject_crlf("To", message.to_address or "")
    _reject_crlf("From-Name", settings.SMTP_FROM_NAME or "")
    _reject_crlf("From-Email", settings.SMTP_FROM_EMAIL or "")

    msg = EmailMessage()
    # multipart/alternative: plain-text part for fallback + a styled HTML part so the
    # email renders like a normal formatted email in modern clients.
    from ..email_html import branded_signature_html, to_html_email

    # Branded signature card (operator-selected); None keeps the default behavior.
    sig_html = branded_signature_html(getattr(message, "signature_key", None))
    msg.set_content(message.body or "", subtype="plain", charset="utf-8")
    msg.add_alternative(
        to_html_email(message.body or "", signature_html=sig_html),
        subtype="html",
        charset="utf-8",
    )
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

    stored_message_id = getattr(message, "smtp_message_id", None)
    if isinstance(stored_message_id, str) and stored_message_id:
        _reject_crlf("Message-ID", stored_message_id)
        message_id = stored_message_id
    else:
        row_id = getattr(message, "id", None)
        message_id = _generate_message_id(row_id if isinstance(row_id, int) else None)
    msg["Message-ID"] = message_id
    return msg


_TRANSIENT_SMTP_CODES = {421, 450, 451, 452, 471}


class SMTPPermanentError(RuntimeError):
    """Bad recipient, auth, malformed — retry won't help."""


class SMTPTransientError(RuntimeError):
    """Connection / temp failure — retry is worth trying."""


class SMTPDeliveryUnknown(RuntimeError):
    """The SMTP DATA exchange may have succeeded; retrying could duplicate mail."""


class SMTPSendingDisabled(SMTPPermanentError):
    """The operator's temporary no-send switch is engaged (safe_mode.EMAIL_SENDING_ENABLED).

    Subclasses SMTPPermanentError so the send worker stops instead of retrying: the
    message is marked send_failed and stays visible, rather than silently looking
    delivered or spinning in a retry loop.
    """


def send_smtp(message: Message) -> None:
    """Send an email via SMTP.

    Classifies SMTP failures into transient vs permanent so the worker can decide
    whether to retry. Header CRLF injection is rejected before connecting.
    """
    # Final safety boundary (pre-launch): no email may reach a real customer.
    # resolve_send_override() is non-empty while external writes are disabled, so
    # force the recipient here too — even a caller that bypassed send() cannot
    # email a customer. send() already redirected on the normal path, so this is
    # a no-op there (to_address already equals the override).
    from ...common.safe_mode import email_sending_enabled, resolve_send_override

    # Hard stop, checked before anything else: while the operator's temporary
    # no-send switch is engaged nothing is emailed at all, not even to the
    # pre-launch test recipient. This is the lowest chokepoint every send path
    # reaches, so no caller can route around it.
    if not email_sending_enabled():
        raise SMTPSendingDisabled(
            "Email sending is disabled in code "
            "(src/common/safe_mode.py: EMAIL_SENDING_ENABLED = False)."
        )

    override = resolve_send_override()
    if override and (message.to_address or "") != override:
        message = copy.copy(message)
        message.to_address = override

    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        raise SMTPPermanentError("SMTP credentials not configured.")

    msg = _build_message(message)
    message_id = msg["Message-ID"]

    server: smtplib.SMTP | None = None
    try:
        try:
            server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30)
            server.starttls(context=ssl.create_default_context())
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        except smtplib.SMTPAuthenticationError as exc:
            raise SMTPPermanentError("SMTP authentication failed") from exc
        except smtplib.SMTPResponseException as exc:
            if exc.smtp_code in _TRANSIENT_SMTP_CODES:
                raise SMTPTransientError(f"transient SMTP setup error {exc.smtp_code}") from exc
            raise SMTPPermanentError(f"permanent SMTP setup error {exc.smtp_code}") from exc
        except (smtplib.SMTPServerDisconnected, TimeoutError, ConnectionError, OSError) as exc:
            raise SMTPTransientError(f"SMTP connection failure: {exc}") from exc

        try:
            server.send_message(msg)
        except smtplib.SMTPRecipientsRefused as exc:
            raise SMTPPermanentError("SMTP rejected every recipient") from exc
        except smtplib.SMTPResponseException as exc:
            if exc.smtp_code in _TRANSIENT_SMTP_CODES:
                raise SMTPTransientError(f"transient SMTP delivery error {exc.smtp_code}") from exc
            raise SMTPPermanentError(f"permanent SMTP delivery error {exc.smtp_code}") from exc
        except (smtplib.SMTPServerDisconnected, TimeoutError, ConnectionError, OSError) as exc:
            raise SMTPDeliveryUnknown("SMTP delivery outcome is unknown") from exc
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    pass

    message.smtp_message_id = message_id

    logger.info("SMTP sent message row=%s, message_id=%s", message.id, message_id)
