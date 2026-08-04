"""On-demand Korean translation for the operator UI.

Translations are produced at view time with the cheap ``flash`` model and cached
in-process keyed by a hash of the source text. This deliberately stores NOTHING
in the database — the web UI can show a Korean view on any deployment without a
schema migration. Already-Korean text is detected heuristically and skipped.
"""

from __future__ import annotations

import hashlib
import logging

from .client import LLMClient

logger = logging.getLogger(__name__)

# text-hash -> Korean translation. Bounded so a long-running process can't leak.
_cache: dict[str, str] = {}
_CACHE_CAP = 5_000


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣" or "ᄀ" <= ch <= "ᇿ" or "㄰" <= ch <= "㆏"


def _hangul_share(text: str | None) -> float | None:
    """Share of the letters that are Hangul, or None when there are no letters to judge.

    Ratio, not presence: an English reply ending in a Korean signature is still mostly
    English. None (a URL, a number, an empty draft) is neither language — callers must
    not translate it in either direction.
    """
    letters = [ch for ch in (text or "").strip() if ch.isalpha()]
    if not letters:
        return None
    return sum(1 for ch in letters if _is_hangul(ch)) / len(letters)


def needs_korean(text: str | None) -> bool:
    """Whether a Korean translation should be SHOWN for this text — i.e. it is not
    already Korean. Used to give the operator a Korean reading of a foreign inquiry."""
    share = _hangul_share(text)
    return share is not None and share < 0.5


def is_mostly_korean(text: str | None) -> bool:
    """Whether this text IS Korean — i.e. there is something to translate OUT of.

    Deliberately not `not needs_korean(...)`. They are both False for text with no
    letters at all, and writing one as the other's negation is how the review screen's
    번역하기 came to test for the opposite of what it meant.
    """
    share = _hangul_share(text)
    return share is not None and share >= 0.5


def to_korean(text: str | None, *, llm: LLMClient | None = None) -> str:
    """Return a Korean translation of ``text`` (cached). Empty string on failure.

    Returns "" for blank input. Callers should gate on :func:`needs_korean` and
    fall back to the original when this is empty. Mixed-language text is fine —
    the prompt keeps already-Korean fragments (e.g. a signature) as-is.
    """
    text = (text or "").strip()
    if not text:
        return ""

    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cached = _cache.get(key)
    if cached is not None:
        return cached

    try:
        client = llm or LLMClient()
        result = client.complete("util/translate_ko", {"text": text}, tier="flash", max_tokens=2000)
        out = (result or "").strip() if isinstance(result, str) else ""
    except Exception:
        logger.warning("Korean translation failed; showing original.", exc_info=True)
        out = ""

    if out:
        if len(_cache) >= _CACHE_CAP:
            _cache.clear()
        _cache[key] = out
    return out


def translate_to(text: str | None, target_code: str, *, llm: LLMClient | None = None) -> str:
    """Translate ``text`` into the language named by ISO code ``target_code``.

    Returns "" on blank input or failure (callers keep the original on empty).
    Used to enforce that an outgoing reply is in the inquiry's language even when
    the drafting model wrote in the wrong one.
    """
    from .language import language_name

    text = (text or "").strip()
    if not text:
        return ""
    try:
        client = llm or LLMClient()
        result = client.complete(
            "util/translate_to",
            {"text": text, "target_language": language_name(target_code)},
            tier="flash",
            max_tokens=2000,
        )
        return (result or "").strip() if isinstance(result, str) else ""
    except Exception:
        logger.warning("Translation to %s failed; keeping original.", target_code, exc_info=True)
        return ""
