"""Read helper for editable email templates (for send-path integration).

The web UI (routes/email_templates.py) owns CRUD; this module is the read side
the send path calls into.
"""

from __future__ import annotations

from .models import EmailTemplate
from .session import SessionLocal

# Branded HTML signatures are keyed with this prefix so the compose-screen picker
# can discover them generically (no hard-coded ko/en list).
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
