"""Deterministic reply-subject handling — "RE:" prefixing with no duplicates.

The operator's rule: a reply subject must start with a single ``RE:`` and a
re-reply must NOT stack more ("RE: RE: ...") . This is enforced here in code, not
asked of the LLM, so it can never drift.

The reply subject is always built from the CUSTOMER's original subject (so the
thread stays correct in their language). When the inbound had no subject at all,
we fall back to a localized generic phrase in the target language — never the
LLM, so the subject is always present and in the right language.
"""

from __future__ import annotations

import re

# A single leading reply/forward prefix, across the languages we handle. Matches
# optional "[2]"/"(2)" counters (Re[2]:) and both ASCII ":" and full-width "：".
_PREFIX = re.compile(
    r"^\s*(re|aw|sv|antw|res|odp|fwd?|fw|"
    r"회신|답장|답신|전달|"
    r"返信|転送|"
    r"回复|回覆|答复|转发|轉寄)"
    r"\s*(?:[\[\(]\d+[\]\)])?\s*[:：]\s*",
    re.IGNORECASE,
)

# Localized generic subject used only when the inbound carried no subject line.
_GENERIC: dict[str, str] = {
    "en": "Your inquiry",
    "ko": "문의 주신 건",
    "ja": "お問い合わせの件",
    "zh": "您的咨询",
    "vi": "Yêu cầu của bạn",
    "th": "คำถามของคุณ",
    "es": "Su consulta",
    "fr": "Votre demande",
    "de": "Ihre Anfrage",
    "pt": "Sua consulta",
    "id": "Pertanyaan Anda",
    "it": "La tua richiesta",
    "ru": "Ваш запрос",
    "ar": "استفسارك",
    "hi": "आपकी पूछताछ",
}


def strip_reply_prefixes(subject: str | None) -> str:
    """Remove every leading Re:/Fwd:/회신: ... prefix, leaving the bare subject."""
    s = (subject or "").strip()
    while True:
        stripped = _PREFIX.sub("", s, count=1)
        if stripped == s:
            return s.strip()
        s = stripped.strip()


def generic_inquiry_subject(target_code: str | None) -> str:
    """A short, neutral subject in the target language for subject-less inbounds."""
    return _GENERIC.get((target_code or "").lower(), _GENERIC["en"])


def reply_subject(original: str | None, *, target_code: str | None = None) -> str:
    """Build the reply subject: exactly one ``RE:`` over the bare base subject.

    - Strips any existing reply/forward prefixes first (no ``RE: RE:`` stacking).
    - When ``original`` is empty/None, uses a localized generic subject in
      ``target_code`` so a subject is always produced in the right language.
    """
    base = strip_reply_prefixes(original)
    if not base:
        base = generic_inquiry_subject(target_code)
    return f"RE: {base}"
