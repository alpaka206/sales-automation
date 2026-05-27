"""Email compliance — footer injection, suppression check, unsubscribe tokens."""

from __future__ import annotations

import hashlib
import hmac
import logging

from ..common.config import settings

logger = logging.getLogger(__name__)

_EU_TLDS = {"de", "fr", "it", "es", "nl", "be", "at", "pl", "se", "dk", "fi", "ie", "pt", "cz", "ro", "hu", "bg", "hr", "sk", "si", "lt", "lv", "ee", "lu", "mt", "cy", "eu"}


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


def _guess_region(to_email: str, country_code: str | None = None) -> str:
    """Guess regulatory region from country code or email TLD."""
    if country_code:
        cc = country_code.lower()
        if cc == "kr":
            return "kr"
        if cc == "us":
            return "us"
        if cc in _EU_TLDS:
            return "eu"
    domain = to_email.rsplit("@", 1)[-1] if "@" in to_email else ""
    tld = domain.rsplit(".", 1)[-1].lower() if "." in domain else ""
    if tld == "kr":
        return "kr"
    if tld in ("us", "com", "net", "org"):
        return "us"
    if tld in _EU_TLDS:
        return "eu"
    return "default"


def build_footer(language: str, to_email: str, country_code: str | None = None) -> str:
    """Build a region- and language-appropriate compliance footer."""
    unsub_url = build_unsub_url(to_email)
    region = _guess_region(to_email, country_code)
    company = settings.COMPANY_NAME
    reg_no = settings.COMPANY_REGISTRATION_NUMBER
    address = settings.COMPANY_ADDRESS
    privacy_url = settings.COMPANY_PRIVACY_POLICY_URL

    lines: list[str] = ["\n\n---"]

    if region == "kr":
        if settings.KOREA_AD_PREFIX_ENABLED:
            lines.append("(광고)")
        lines.append(f"이 메일은 {company}({settings.SMTP_FROM_EMAIL or 'devrel.365@gmail.com'})의 영업 안내입니다.")
        if reg_no:
            lines.append(f"사업자등록번호: {reg_no}")
        if address:
            lines.append(f"주소: {address}")
        lines.append(f"수신 거부: {unsub_url}")
    elif region == "eu":
        lines.append(f"This email is sent by {company}.")
        if address:
            lines.append(f"Address: {address}")
        lines.append("Legal basis for processing: legitimate interest (GDPR Art. 6(1)(f)).")
        if privacy_url:
            lines.append(f"Privacy policy: {privacy_url}")
        lines.append(f"Unsubscribe: {unsub_url}")
    elif region == "us":
        lines.append(f"This email is sent by {company}.")
        if address:
            lines.append(f"Physical address: {address}")
        lines.append(f"Unsubscribe: {unsub_url}")
    else:
        if language == "ko":
            lines.append(f"이 메일은 {company}의 영업 안내입니다.")
            lines.append(f"수신 거부: {unsub_url}")
        else:
            lines.append(f"This email is a sales outreach from {company}.")
            lines.append(f"Unsubscribe: {unsub_url}")

    return "\n".join(lines) + "\n"


def append_footer(body: str, to_email: str, language: str = "ko", country_code: str | None = None) -> str:
    """Append compliance footer with unsubscribe link to the message body."""
    footer = build_footer(language, to_email, country_code)
    return body + footer


def add_ad_prefix(subject: str, language: str = "ko") -> str:
    """Prepend advertising prefix to subject if Korea compliance is enabled."""
    if not settings.KOREA_AD_PREFIX_ENABLED:
        return subject
    if language == "ko":
        return f"(광고) {subject}" if not subject.startswith("(광고)") else subject
    return f"[AD] {subject}" if not subject.startswith("[AD]") else subject


def is_suppressed(email: str) -> bool:
    """Check if an email is in the suppression list."""
    from ..db.models import EmailSuppression
    from ..db.session import SessionLocal

    try:
        with SessionLocal() as session:
            return session.get(EmailSuppression, email.lower().strip()) is not None
    except Exception:
        logger.warning("suppression check failed for %s", email, exc_info=True)
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
