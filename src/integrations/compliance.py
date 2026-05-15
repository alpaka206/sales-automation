"""Email compliance — footer injection, suppression check, unsubscribe tokens."""

from __future__ import annotations

import hashlib
import hmac
import logging

from ..common.config import settings

logger = logging.getLogger(__name__)

_FOOTER_KO = (
    "\n\n---\n"
    "이 메일은 perso(devrel.365@gmail.com)의 영업 안내입니다.\n"
    "수신 거부: {unsub_url}\n"
)

_FOOTER_EN = (
    "\n\n---\n"
    "This email is a sales outreach from perso (devrel.365@gmail.com).\n"
    "Unsubscribe: {unsub_url}\n"
)


def generate_unsub_token(email: str) -> str:
    """HMAC-SHA256 token for unsubscribe link verification."""
    secret = settings.INTERNAL_API_TOKEN or "fallback-secret"
    return hmac.new(secret.encode(), email.lower().encode(), hashlib.sha256).hexdigest()[:32]


def verify_unsub_token(email: str, token: str) -> bool:
    """Verify an unsubscribe token matches the email."""
    expected = generate_unsub_token(email)
    return hmac.compare_digest(expected, token)


def build_unsub_url(email: str) -> str:
    """Build the unsubscribe URL for a given email address."""
    token = generate_unsub_token(email)
    base = f"http://{settings.APP_HOST}:{settings.APP_PORT}"
    return f"{base}/unsubscribe?email={email}&token={token}"


def append_footer(body: str, to_email: str, language: str = "ko") -> str:
    """Append compliance footer with unsubscribe link to the message body."""
    unsub_url = build_unsub_url(to_email)
    if language == "ko" or language not in ("en", "ja", "es", "pt", "zh", "de", "fr"):
        footer = _FOOTER_KO.format(unsub_url=unsub_url)
    else:
        footer = _FOOTER_EN.format(unsub_url=unsub_url)
    return body + footer


def is_suppressed(email: str) -> bool:
    """Check if an email is in the suppression list."""
    from ..db.models import EmailSuppression
    from ..db.session import SessionLocal

    try:
        with SessionLocal() as session:
            return session.get(EmailSuppression, email.lower().strip()) is not None
    except Exception:
        return False


def suppress_email(email: str, reason: str = "unsubscribe") -> None:
    """Add an email to the suppression list."""
    from ..db.models import EmailSuppression
    from ..db.session import SessionLocal

    with SessionLocal() as session:
        existing = session.get(EmailSuppression, email.lower().strip())
        if not existing:
            session.add(EmailSuppression(email=email.lower().strip(), reason=reason))
            session.commit()
            logger.info("Suppressed email %s (reason: %s)", email, reason)
