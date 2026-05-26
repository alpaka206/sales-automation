"""Gmail IMAP client for reply detection."""

from __future__ import annotations

import email
import email.utils
import imaplib
import logging
from datetime import datetime, timezone
from email.header import decode_header

from ..common.config import settings

logger = logging.getLogger(__name__)


class IMAPNotConfigured(RuntimeError):
    pass


class IMAPAuthError(RuntimeError):
    """Bad credentials, expired app password — operator action required."""


class IMAPClient:
    """Reads Gmail inbox via IMAP to detect replies to outbound messages."""

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        folder: str | None = None,
    ) -> None:
        self.username = username or settings.GMAIL_IMAP_USERNAME
        self.password = password or settings.GMAIL_IMAP_PASSWORD
        self.folder = folder or settings.GMAIL_IMAP_FOLDER
        if not self.username or not self.password:
            raise IMAPNotConfigured("GMAIL_IMAP_USERNAME and GMAIL_IMAP_PASSWORD required.")

    def fetch_replies(self, since_dt: datetime) -> list[dict]:
        """Fetch emails received since the given datetime."""
        date_str = since_dt.strftime("%d-%b-%Y")
        results: list[dict] = []

        try:
            conn = imaplib.IMAP4_SSL("imap.gmail.com")
            conn.login(self.username, self.password)
            conn.select(self.folder)

            _, data = conn.search(None, f'(SINCE {date_str})')
            msg_ids = data[0].split() if data[0] else []

            for msg_id in msg_ids:
                _, msg_data = conn.fetch(msg_id, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue

                raw = msg_data[0][1]
                if isinstance(raw, bytes):
                    parsed = email.message_from_bytes(raw)
                else:
                    parsed = email.message_from_string(raw)

                from_addr = _extract_email_addr(parsed.get("From", ""))
                message_id = parsed.get("Message-ID", "")
                in_reply_to = parsed.get("In-Reply-To", "")
                references = parsed.get("References", "")
                subject = _decode_subject(parsed.get("Subject", ""))
                date_str_hdr = parsed.get("Date", "")
                received_at = _parse_date(date_str_hdr)
                body_snippet = _extract_body_snippet(parsed)

                results.append({
                    "message_id": message_id.strip(),
                    "in_reply_to": in_reply_to.strip(),
                    "references": references.strip(),
                    "from_addr": from_addr,
                    "subject": subject,
                    "body_snippet": body_snippet,
                    "received_at": received_at,
                })

            conn.close()
            conn.logout()
        except IMAPNotConfigured:
            raise
        except Exception:
            logger.warning("IMAP fetch failed.", exc_info=True)

        logger.info("IMAP: fetched %d emails since %s.", len(results), date_str)
        return results


def _extract_email_addr(raw: str) -> str:
    """Extract email address from a 'Name <email>' string."""
    _, addr = email.utils.parseaddr(raw)
    return addr.lower()


def _decode_subject(raw: str) -> str:
    """Decode MIME-encoded subject header."""
    parts = decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _parse_date(date_str: str) -> datetime | None:
    """Parse email Date header."""
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _extract_body_snippet(msg: email.message.Message, max_chars: int = 200) -> str:
    """Extract first text/plain part, truncated."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")[:max_chars]
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace")[:max_chars]
    return ""
