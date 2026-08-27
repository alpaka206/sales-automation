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

# 발송 경로가 **이름으로 찾는** 키. 화면이 이 행에 표를 답니다.
#
# 이 표가 필요한 이유: 이제 콘솔에서 아무 키나 만들고 무엇이든 지울 수 있습니다(운영자 결정,
# 2026-08-18). 자유롭게 하면 두 가지가 조용해집니다 — 만든 행은 읽는 코드가 없어 목록에만
# 존재하고, 지운 행은 조회만 남기고 사라집니다(예: 회신이 ``{{MEETING_LINK}}`` 로
# 끝납니다). 막지 않기로 했으므로 **보이게** 합니다: 목록의
# 표와 삭제 확인 창의 경고가 같은 이 함수에서 나옵니다.
_CODE_RESOLVED_KEYS = frozenset(
    {
        "reply_format",
        "reply_format_en",
        "meeting_link",
        "meeting_link_en",
        "whatsapp_link",
        "whatsapp_link_en",
        "sender_name",
        "sender_name_en",
    }
)

def is_code_resolved(key: str) -> bool:
    """그 행을 발송 경로가 이름으로 찾는가. 지울 때 무엇이 없어지는지가 여기서 나옵니다."""
    key = key or ""
    return key in _CODE_RESOLVED_KEYS or key.startswith(SIGNATURE_KEY_PREFIX)


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
                )
                .order_by(EmailTemplate.name)
                .first()
            )
            return row.key if row else None
    except Exception:
        logger.warning("Default signature lookup failed", exc_info=True)
        return None


def get_email_template(key: str, language: str | None = None) -> str | None:
    """Return the active template body for ``key``, or None if not found.

    Only ``status="active"`` rows are considered. When ``language`` is given, a
    row whose ``language`` matches it wins; otherwise it falls back to a row with
    ``language="all"``. With no ``language``, any active row for the key is used
    (preferring an exact ``"all"`` match for determinism).

    That fallback needs an ``all`` row to exist. Most keys have exactly one row and no
    caller passes a language for them — the language lives in the KEY (``reply_format``
    vs ``reply_format_en``), and the send path picks the key.
    """
    with SessionLocal() as session:
        rows = (
            session.query(EmailTemplate)
            .filter(EmailTemplate.key == key)
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
