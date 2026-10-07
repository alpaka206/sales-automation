"""Deterministic money/price detection.

지금 쓰는 곳은 제목 고르개 하나입니다(``subjects.valid_subject`` — 첫 회신의 새 제목에 금액이 있으면 그
제목을 안 씁니다). 본문의 금액은 코드가 지우지도 운영자에게 짚지도 않습니다 (2026-10-06): 예전에는 초안과
발송 두 곳에서 그 줄을 **지웠는데**, 줄을 맞춰 쓴 항목의 한 줄만 빠져 고아 줄이 남았고(MSG#110),
콘솔 문서가 첫 회신에 써도 된다고 한 셀프서브 공개 가격까지 사라져 문장이 끊긴 채 나갔습니다(MSG#118).
지우는 대신 경고로 짚지도 않습니다 — 운영자: 「답변 작성에 대한 경고문 이런건 필요없어」.

Detection requires a *currency next to a number* — a symbol ($29, 29€), a code
(USD 29, 99k KRW) or a word (29 dollars, 99,000원). Plain quantities like
"200 mins", "60-minute videos", "Tier 2", "2026", **"minutes per month"**,
**"1,000/month"** or a Korean address ("제2동") are never mistaken for prices — those
are scoping questions, and the old ``per month`` / ``N/mo`` rules flagged them.
"""

from __future__ import annotations

import re

# Currency symbols next to a digit: $29, ₩99,000, €10, ¥500, ฿300, ₫…, 29€
_SYM = r"[$₩€£¥₫฿₹]"

_PATTERNS = [
    rf"{_SYM}\s?\d",  # $29, ₩ 99,000
    rf"\d\s?{_SYM}",  # 29€, 99 €
    r"\d[\d,.\s]*\s?k?\s?(?:USD|KRW|EUR|JPY|GBP|VND|THB|RMB|CNY|SGD|AUD|CAD|won)\b",  # 99k KRW, 29 USD
    r"\b(?:USD|KRW|EUR|JPY|GBP|VND|THB|RMB|CNY|SGD|AUD|CAD)\s?\d",  # USD 29
    # 99,000원, 3만원, 3만 원, 10달러. 「동」(VND)은 뺐습니다 — 「제2동」 같은 주소가 걸립니다.
    r"\d[\d,.]*\s?(?:만\s?원|천\s?원|원|달러|엔|위안|유로|파운드)",
    # English spelled-out currency words next to a number (29 dollars, 50 euros…).
    r"\d[\d,.]*\s?(?:dollars?|euros?|pounds?|yen|cents?|bucks?)\b",
]
_PRICE_RE = re.compile("|".join(_PATTERNS), re.IGNORECASE)


def contains_price(text: str | None) -> bool:
    """True if ``text`` states a concrete monetary amount (a currency next to a number)."""
    if not text:
        return False
    return bool(_PRICE_RE.search(text))
