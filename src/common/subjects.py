"""Deterministic reply-subject handling — "RE:" prefixing with no duplicates.

The operator's rule: a reply subject must start with a single ``RE:`` and a
re-reply must NOT stack more ("RE: RE: ...") . This is enforced here in code, not
asked of the LLM, so it can never drift.

**Which subject** a draft gets is ``choose_reply_subject`` (2026-10-06). It used to be
``RE: <HubSpot ticket name>`` for every reply — but the ticket name is an internal record
name: chat tickets are 「[Chatbot] 문의 접수」, CS triage renames form tickets
(「[Form] … > 엔터프라이즈 전달」, 「맞춤형 플랜 문의」), and those labels went out to Hindi and
English customers (msg 72 · 110). Now: an e-mail thread that already exists keeps its subject
(``RE: <it>``); a new thread gets a checked model proposal, else the customer's own form
subject, else a localized generic phrase. The ticket name is never a candidate.
"""

from __future__ import annotations

import re

from .pricing_guard import contains_price

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


_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_TAG = re.compile(r"\[[^\[\]]*\]|【[^【】]*】")
# 나갈 언어가 요구하는 문자. 여기 없는 언어(en · es · pt · vi …)는 라틴 문자만 받습니다.
_NEEDS = {"ko": {"ko"}, "ja": {"kana", "han"}, "zh": {"han"}, "th": {"th"}, "ru": {"ru"}, "ar": {"ar"},
          "hi": {"hi"}}


def _script(ch: str) -> str:
    o = ord(ch)
    if 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
        return "ko"
    if 0x3040 <= o <= 0x30FF:
        return "kana"
    if 0x4E00 <= o <= 0x9FFF:
        return "han"
    for script, low, high in (("th", 0x0E00, 0x0E7F), ("ru", 0x0400, 0x04FF), ("ar", 0x0600, 0x06FF),
                              ("hi", 0x0900, 0x097F)):
        if low <= o <= high:
            return script
    return "latin"


def _script_matches(text: str, target: str | None) -> bool:
    code = (target or "").strip().lower()[:2]
    if not code:
        return True
    found = {_script(ch) for ch in text if ch.isalpha()}
    if "ko" in found and code != "ko":
        return False  # 한국어가 아닌 메일에 한글 제목 — 내부 이름이 새는 모양이 이것입니다(msg 72 · 110)
    needs = _NEEDS.get(code)
    if needs is None:
        return found <= {"latin"}
    return bool(found & needs) and not (code == "zh" and "kana" in found)


def valid_subject(subject: str | None, *, target: str | None, first_reply: bool, console_text: str = "") -> str:
    """새 스레드에 쓸 수 있는 제목이면 다듬은 제목, 아니면 "".

    한 줄 · RE:/Fwd: 없음 · 3~120자 · 주소/URL/``{{`` 없음 · 나갈 언어의 문자 · 첫 회신이면 금액 없음 ·
    ``[…]``/``【…】`` 꼬리표는 콘솔 글(이 초안이 본 회사 문서와 서식)에 그대로 있는 것만. 마지막 것이
    「[Form]」·「[Chatbot]」 같은 내부 꼬리표를 거릅니다 — 회사 문서가 「[Perso Dubbing] …」을 쓰면 그건 받습니다.
    고치지 않고 거절합니다: 고친 제목은 아무도 안 쓴 문장입니다.
    """
    text = strip_reply_prefixes(" ".join(str(subject or "").split()))
    if not 3 <= len(text) <= 120 or "@" in text or "{{" in text or _URL.search(text):
        return ""
    if any(tag not in (console_text or "") for tag in _TAG.findall(text)):
        return ""
    if not _script_matches(text, target) or (first_reply and contains_price(text)):
        return ""
    return text


def choose_reply_subject(
    *,
    thread_subject: str | None,
    proposed: str | None = None,
    customer_subject: str | None = None,
    target: str | None = None,
    first_reply: bool = False,
    console_text: str = "",
) -> tuple[str, str]:
    """(제목, 출처) — 출처는 ``thread`` · ``model`` · ``customer`` · ``generic``.

    1. 이 대화에 이미 오간 이메일이 있으면 그 제목에 ``RE:`` 하나 — 고객 메일함의 같은 스레드에 붙습니다.
       검사하지 않습니다: 고객이 이미 그 제목의 스레드를 들고 있습니다.
    2. 없으면 새 스레드라 ``RE:`` 없이, ``valid_subject`` 를 지난 첫 후보 — 모델이 제안한 제목, 고객이
       폼에 쓴 제목. 둘 다 아니면 나갈 언어의 기본 제목입니다.

    허브스팟 티켓 이름은 후보가 아닙니다(모듈 설명). 줄바꿈은 어디서 오든 한 칸으로 접습니다 — 발송이
    제목의 CR/LF 를 거절합니다.
    """
    base = strip_reply_prefixes(" ".join(str(thread_subject or "").split()))
    if base:
        return f"RE: {base}", "thread"
    for source, candidate in (("model", proposed), ("customer", customer_subject)):
        chosen = valid_subject(candidate, target=target, first_reply=first_reply, console_text=console_text)
        if chosen:
            return chosen, source
    return generic_inquiry_subject(target), "generic"
