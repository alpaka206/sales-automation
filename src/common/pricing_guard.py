"""Deterministic "no pricing in the first reply" enforcement.

The operator's rule: the FIRST reply to a new inbound must not state any concrete
amount (it may mention that a special promotion exists, just not numbers). Rather
than trust the draft prompt to obey, we detect money/price patterns in code and
strip the offending lines from a first-reply draft, returning what was removed so
the UI can flag it.

Detection requires a *currency or per-period signal next to a number* so plain
quantities like "200 mins", "60-minute videos", "Tier 2", or the year "2026" are
never mistaken for prices.
"""

from __future__ import annotations

import re

from .textwash import text_wash

# Currency symbols immediately before a digit: $29, ₩99,000, €10, ¥500, ฿300, ₫…
_SYM = r"[$₩€£¥₫฿₹]"

_PATTERNS = [
    rf"{_SYM}\s?\d",  # $29, ₩ 99,000
    r"\d[\d,.\s]*\s?k?\s?(?:USD|KRW|EUR|JPY|GBP|VND|THB|RMB|CNY|SGD|AUD|CAD|won)\b",  # 99k KRW, 29 USD
    r"\b(?:USD|KRW|EUR|JPY|GBP|VND|THB|RMB|CNY|SGD|AUD|CAD)\s?\d",  # USD 29
    r"\d[\d,.]*\s?(?:원|달러|엔|위안|유로|파운드|동|만원|천원)",  # 99,000원, 3만원, 10달러
    # English spelled-out currency words next to a number (29 dollars, 50 euros…).
    r"\d[\d,.]*\s?(?:dollars?|euros?|pounds?|yen|cents?|bucks?)\b",
    r"\d[\d,.]*\s?/\s?(?:mo|month|mth|yr|year|seat|user|월|개월|년)\b",  # 29/mo, 10/월
    r"\bper\s+(?:month|year|seat|user|account)\b",  # per month/seat (price context)
]
_PRICE_RE = re.compile("|".join(_PATTERNS), re.IGNORECASE)


def contains_price(text: str | None) -> bool:
    """True if ``text`` states a concrete monetary amount or per-period price."""
    if not text:
        return False
    return bool(_PRICE_RE.search(text))


def strip_price_sentences(text: str | None) -> tuple[str, list[str]]:
    """Remove every LINE that states a price; return (cleaned_text, removed_lines).

    Works line-by-line because the reply formatting rule puts one fact/idea per
    line (and prices in bullet rows), so a line is the right unit to drop without
    mangling the rest of the message. The result is re-washed so no blank holes
    are left behind.
    """
    if not text:
        return "", []
    kept: list[str] = []
    removed: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.strip() and contains_price(line):
            removed.append(line.strip())
        else:
            kept.append(line)
    if not removed:
        return text, []
    return text_wash("\n".join(kept)), removed
