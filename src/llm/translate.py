"""On-demand Korean translation for the operator UI.

Translations are produced at view time with the cheap ``flash`` model and cached
in-process keyed by a hash of the source text. This deliberately stores NOTHING
in the database — the web UI can show a Korean view on any deployment without a
schema migration. Already-Korean text is detected heuristically and skipped.
"""

from __future__ import annotations

import hashlib
import logging
import re

from .client import LLMClient

logger = logging.getLogger(__name__)

# 번역에 **넘기지 않는** 것: 주소와 토큰.
#
# 예약 주소는 120자짜리 base64 입니다. 초안 단계에서는 이것을 모델에게서 지켰습니다 —
# 모델은 `{{MEETING_LINK}}` 만 출력하고 발송 직전에 진짜 값으로 바뀝니다(`apply_editable_
# tokens`). 그런데 그 치환이 **번역보다 앞**에 있습니다: 영문 고객에게 나갈 때
# `enforce_send_language` 가 본문 전체를 다시 모델에 넣고, 그 안에 이미 주소가 들어 있습니다.
# 마지막 한 구간에서 보호가 풀립니다. 프롬프트에 "URL 은 그대로 두라" 고 적혀 있지만 그건
# 지시일 뿐이고, 같은 프롬프트에 "No markdown" 이라는 줄도 있어서 `[글자](주소)` 를 풀어
# 버릴 여지까지 있습니다.
#
# 그래서 코드로 뺍니다. 자리표시자는 글자가 없어야 번역 대상이 되지 않습니다.
_PROTECT_RE = re.compile(r"https?://[^\s<>\"']+|\{\{[A-Za-z_]+\}\}")
# 되돌릴 때는 느슨하게 봅니다 — 모델이 자리표시자 주위에 공백을 넣는 정도는 흔합니다.
_RESTORE_RE = re.compile(r"%%\s*(\d+)\s*%%")


def _protect(text: str) -> tuple[str, list[str]]:
    """주소·토큰을 자리표시자로 빼 둡니다. 모델은 껍데기만 봅니다."""
    held: list[str] = []

    def _hold(match: re.Match) -> str:
        held.append(match.group(0))
        return f"%%{len(held) - 1}%%"

    return _PROTECT_RE.sub(_hold, text), held


def _restore(text: str, held: list[str]) -> str | None:
    """자리표시자를 원래 값으로. **하나라도 안 돌아왔으면 None** 입니다.

    없어진 자리표시자는 곧 없어진 링크입니다. 그것을 그대로 내보내면 고객은 예약 링크가
    빠진 메일을 받고, 화면에도 로그에도 아무 표가 없습니다 — 사람이 발송을 누른 **뒤**에
    도는 단계라 검토에도 안 걸립니다. 언어가 틀린 메일은 눈에 띄어 고쳐지지만 링크가 빠진
    메일은 안 띕니다. 그래서 의심스러우면 번역을 실패로 칩니다(부르는 쪽은 원문을 씁니다).
    """
    if not held:
        return text
    seen: set[int] = set()

    def _put(match: re.Match) -> str:
        index = int(match.group(1))
        if index >= len(held):
            return match.group(0)
        seen.add(index)
        return held[index]

    restored = _RESTORE_RE.sub(_put, text)
    if len(seen) != len(held):
        return None
    return restored

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

    masked, held = _protect(text)
    try:
        client = llm or LLMClient()
        result = client.complete("util/translate_ko", {"text": masked}, tier="flash", max_tokens=2000)
        out = (result or "").strip() if isinstance(result, str) else ""
    except Exception:
        logger.warning("Korean translation failed; showing original.", exc_info=True)
        out = ""

    if out:
        restored = _restore(out, held)
        if restored is None:
            logger.warning(
                "Korean translation dropped %d protected value(s); showing original.", len(held)
            )
        out = restored or ""

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

    URLs and ``{{TOKENS}}`` never reach the model — see ``_protect``. A translation that
    comes back missing one is treated as a failure, not silently shipped.
    """
    from .language import language_name

    text = (text or "").strip()
    if not text:
        return ""
    masked, held = _protect(text)
    try:
        client = llm or LLMClient()
        result = client.complete(
            "util/translate_to",
            {"text": masked, "target_language": language_name(target_code)},
            tier="flash",
            max_tokens=2000,
        )
        out = (result or "").strip() if isinstance(result, str) else ""
    except Exception:
        logger.warning("Translation to %s failed; keeping original.", target_code, exc_info=True)
        return ""
    if not out:
        return ""
    restored = _restore(out, held)
    if restored is None:
        # 링크가 빠진 채로 고객에게 나가느니 번역이 실패한 것으로 칩니다. 부르는 쪽
        # (`enforce_send_language`) 은 원문을 그대로 보내고 경고를 남깁니다.
        logger.warning(
            "Translation to %s dropped %d protected value(s) (URL/token); keeping original.",
            target_code,
            len(held),
        )
        return ""
    return restored
