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


def needs_korean(text: str | None) -> bool:
    """Whether a Korean translation should be shown for this text.

    Ratio-based, not presence-based: an English reply that ends with a Korean
    signature is still mostly English and should be translated. Only text that is
    *predominantly* Korean (>= half of its letters are Hangul) is treated as
    already-Korean and skipped.
    """
    text = (text or "").strip()
    if not text:
        return False
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    hangul = sum(1 for ch in letters if _is_hangul(ch))
    return (hangul / len(letters)) < 0.5


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
        result = client.complete(
            "util/translate_ko", {"text": text}, tier="flash", max_tokens=2000
        )
        out = (result or "").strip() if isinstance(result, str) else ""
    except Exception:
        logger.warning("Korean translation failed; showing original.", exc_info=True)
        out = ""

    if out:
        if len(_cache) >= _CACHE_CAP:
            _cache.clear()
        _cache[key] = out
    return out
