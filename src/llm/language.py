"""Deterministic-ish language detection for inbound inquiries.

The reply must go out in the SAME language the customer wrote in (an English
inquiry must never get a Korean reply). Relying on the drafting model to both
detect and write in the right language proved unreliable, so we detect the
inquiry language up front and pass it to the draft prompt as a hard constraint.

Strategy: cheap script-based heuristics settle the common, unambiguous cases
(Korean / Japanese / Thai / Chinese) with no LLM call; Latin-script text is
disambiguated (en vs vi/es/…) with the flash model, falling back to English.
"""

from __future__ import annotations

import logging

from .client import LLMClient

logger = logging.getLogger(__name__)

# ISO 639-1 -> English name, used to give the draft prompt an unambiguous target.
_LANG_NAMES = {
    "en": "English",
    "ko": "Korean",
    "ja": "Japanese",
    "zh": "Chinese",
    "vi": "Vietnamese",
    "th": "Thai",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "id": "Indonesian",
    "it": "Italian",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
}

_DEFAULT = "en"


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣" or "ᄀ" <= ch <= "ᇿ" or "㄰" <= ch <= "㆏"


def _script_guess(text: str) -> str | None:
    """Return a language code from non-Latin script signals, or None if unsure.

    Checks Korean first (the case we most need to get right), then kana, Thai,
    and bare CJK ideographs. Latin-script languages return None and are left to
    the LLM step.
    """
    letters = [ch for ch in text if ch.isalpha()]
    if letters:
        hangul = sum(1 for ch in letters if _is_hangul(ch))
        if hangul / len(letters) >= 0.5:
            return "ko"

    for ch in text:
        o = ord(ch)
        if 0x3040 <= o <= 0x30FF:  # hiragana / katakana
            return "ja"
        if 0x0E00 <= o <= 0x0E7F:  # Thai
            return "th"

    # CJK ideographs with no kana → treat as Chinese.
    if any(0x4E00 <= ord(ch) <= 0x9FFF for ch in text):
        return "zh"

    return None


def detect_language(text: str | None, *, llm: LLMClient | None = None) -> str:
    """Best-effort ISO 639-1 code for the language ``text`` is written in.

    Never raises — defaults to English on any failure so a reply still goes out.
    """
    text = (text or "").strip()
    if not text:
        return _DEFAULT

    guess = _script_guess(text)
    if guess:
        return guess

    try:
        out = (llm or LLMClient()).complete(
            "util/detect_language", {"text": text[:2000]}, tier="flash", max_tokens=8
        )
        code = (out or "").strip().lower() if isinstance(out, str) else ""
        # Keep only a leading 2-letter alpha code (model may add stray chars).
        code = code[:2]
        if len(code) == 2 and code.isalpha():
            return code
    except Exception:
        logger.warning("LLM language detection failed; defaulting to %s.", _DEFAULT, exc_info=True)

    return _DEFAULT


def language_label(code: str) -> str:
    """Human-readable target for the draft prompt, e.g. 'English (en)'."""
    code = (code or "").lower()
    name = _LANG_NAMES.get(code)
    return f"{name} ({code})" if name else code


def language_name(code: str) -> str:
    """English name of a language code (e.g. 'en' → 'English'), or the code itself."""
    return _LANG_NAMES.get((code or "").lower(), code or "")
