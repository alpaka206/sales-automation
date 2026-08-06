"""Read helper for editable email templates (for send-path integration).

The web UI (routes/email_templates.py) owns CRUD; this module is the read side
the send path calls into.
"""

from __future__ import annotations

import logging

from .models import EmailTemplate
from .session import SessionLocal

logger = logging.getLogger(__name__)

# 서명은 이 접두사 하나로 알아봅니다 — 목록의 서명 묶음, 검토 화면의 고르개, 지울 수 있는
# 행이 전부 같은 집합입니다. 예전에는 ``signature_html_`` (고르개) 와 ``signature_`` (목록)
# 두 가지였고, 그래서 화면에는 서명으로 보이는데 고를 수는 없는 행이 존재했습니다.
SIGNATURE_KEY_PREFIX = "signature_"

# 접수확인 아래에 붙는 것. 서명이 아니라 로고 한 줄이고, 그래서 접두사 밖에 있습니다 —
# 검토 화면의 서명 고르개에 나오면 안 됩니다. 붙는 자리와 방법은 서명과 같습니다
# (``messages.signature_key`` → ``branded_signature_html``): 본문 아래 붙는 블록이라는
# 뜻의 열이지, 서명 전용 열이 아닙니다.
AUTO_ACK_FOOTER_KEY = "auto_ack_footer"


def list_signature_templates() -> list[dict]:
    """Active signature templates, for the review screen's picker.

    Returns ``[{"key", "name"}, ...]`` ordered by name. No language: nothing matches a
    signature to a language — the operator picks one on the draft — and a column only the
    list could show is a question with no answer.

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
                .order_by(EmailTemplate.name)
                .all()
            )
            return [{"key": r.key, "name": r.name} for r in rows]
    except Exception:
        return []


def default_signature_key() -> str | None:
    """The signature a new draft starts on — **the first one in the list.**

    There is no "default" flag any more (0060). There was one, and keeping it meant
    storing which row is default, an index to guarantee only one is, and a button and a
    route to move it — for a value the operator can already change on the draft itself.

    So the rule is just "the first one", by the same ordering the console shows. Which
    signature a mail actually goes out with stays a per-draft choice on the review screen;
    this only decides where that choice starts.

    Never raises: None means the mail goes out unsigned, which is a real answer now that
    nothing writes a signature into the body behind the operator's back (0061).
    """
    try:
        with SessionLocal() as session:
            row = (
                session.query(EmailTemplate)
                .filter(
                    EmailTemplate.key.like(f"{SIGNATURE_KEY_PREFIX}%"),
                    EmailTemplate.status == "active",
                )
                .order_by(EmailTemplate.name)
                .first()
            )
            return row.key if row else None
    except Exception:
        logger.warning("Default signature lookup failed", exc_info=True)
        return None


def get_email_subject(key: str) -> str | None:
    """That template's mail subject, or None.

    Its own lookup rather than a second return value from ``get_email_template``: every
    other caller wants the body and nothing else, and the acknowledgement is the only
    template that is a whole mail.
    """
    try:
        with SessionLocal() as session:
            row = (
                session.query(EmailTemplate)
                .filter(EmailTemplate.key == key, EmailTemplate.status == "active")
                .first()
            )
            return (row.subject or "").strip() or None if row else None
    except Exception:
        logger.warning("Subject lookup failed for %s", key, exc_info=True)
        return None


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
