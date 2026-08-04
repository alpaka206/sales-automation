"""Read helper for editable email templates (for send-path integration).

The web UI (routes/email_templates.py) owns CRUD; this module is the read side
the send path calls into.
"""

from __future__ import annotations

import logging

from .models import EmailTemplate
from .session import SessionLocal

# Branded HTML signatures are keyed with this prefix so the compose-screen picker
# can discover them generically (no hard-coded ko/en list).
logger = logging.getLogger(__name__)

SIGNATURE_KEY_PREFIX = "signature_html_"


def list_signature_templates() -> list[dict]:
    """Active branded HTML signature templates, for the compose-screen picker.

    Returns ``[{"key", "name", "language"}, ...]`` ordered by language then name.
    Never raises — a DB hiccup yields an empty list so the page still renders.
    """
    try:
        with SessionLocal() as session:
            rows = (
                session.query(EmailTemplate)
                .filter(
                    EmailTemplate.key.like(f"{SIGNATURE_KEY_PREFIX}%"),
                    EmailTemplate.status == "active",
                    EmailTemplate.channel == "email",
                )
                .order_by(EmailTemplate.language, EmailTemplate.name)
                .all()
            )
            return [
                {"key": r.key, "name": r.name, "language": r.language or "all"} for r in rows
            ]
    except Exception:
        return []


def all_text_signatures() -> list[str]:
    """Every plain-text signature body ever configured, for stripping.

    Not filtered by status: a draft written while a template was active still ends with
    that block after it is paused, and it still has to come off when a branded HTML
    signature replaces it.
    """
    try:
        with SessionLocal() as session:
            rows = (
                session.query(EmailTemplate)
                .filter(EmailTemplate.key.in_(("signature_ko", "signature_en")))
                .all()
            )
            return [r.body.strip() for r in rows if (r.body or "").strip()]
    except Exception:
        logger.warning("Text signature lookup failed", exc_info=True)
        return []


def default_signature_key() -> str | None:
    """The signature a new draft is stamped with, as chosen in the console.

    Was a literal signature key, written twice into the inbound agent — so
    the person who signs the company's mail could only be changed by editing Python.

    Falls back to the first active signature when nothing is flagged, because a draft
    with no signature at all is worse than one signed by whoever is left. Never raises:
    None means "sign nothing", which the send path already handles.
    """
    try:
        with SessionLocal() as session:
            row = (
                session.query(EmailTemplate)
                .filter(
                    EmailTemplate.key.like(f"{SIGNATURE_KEY_PREFIX}%"),
                    EmailTemplate.status == "active",
                    EmailTemplate.is_default.is_(True),
                )
                .first()
            )
            if row is None:
                row = (
                    session.query(EmailTemplate)
                    .filter(
                        EmailTemplate.key.like(f"{SIGNATURE_KEY_PREFIX}%"),
                        EmailTemplate.status == "active",
                    )
                    .order_by(EmailTemplate.language, EmailTemplate.name)
                    .first()
                )
            return row.key if row else None
    except Exception:
        logger.warning("Default signature lookup failed", exc_info=True)
        return None


def set_default_signature(key: str) -> None:
    """Move the default. One statement clears, one sets — the unique index in the
    database is what actually guarantees a single default, not this ordering."""
    with SessionLocal() as session:
        session.query(EmailTemplate).filter(EmailTemplate.is_default.is_(True)).update(
            {"is_default": False}
        )
        session.query(EmailTemplate).filter(EmailTemplate.key == key).update(
            {"is_default": True}
        )
        session.commit()


def get_email_template(key: str, language: str | None = None) -> str | None:
    """Return the active template body for ``key``, or None if not found.

    Only ``status="active"`` rows are considered. When ``language`` is given, a
    row whose ``language`` matches it wins; otherwise it falls back to a row with
    ``language="all"``. With no ``language``, any active row for the key is used
    (preferring an exact ``"all"`` match for determinism).
    """
    with SessionLocal() as session:
        rows = (
            session.query(EmailTemplate)
            .filter(EmailTemplate.key == key, EmailTemplate.status == "active")
            .all()
        )
        if not rows:
            return None
        by_lang = {r.language: r for r in rows}
        if language and language in by_lang:
            return by_lang[language].body
        if "all" in by_lang:
            return by_lang["all"].body
        if language is None:
            return rows[0].body
        return None
