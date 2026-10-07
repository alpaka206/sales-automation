"""채우지 않은 자리 — 회신 본문에 남은 검사는 이것 하나입니다 (2026-10-06).

운영자: 「답변 작성에 대한 경고문 이런건 필요없어」. 초안은 전부 운영자가 읽고 고치고 보냅니다. 그래서
초안의 품질을 짚는 표시(첫 회신의 금액 · 대외 비공개 용어 · 처리 완료 표현 같은 것)는 두지 않고, 승인에
「경고를 확인함」 단계도 없습니다 — 되살리기 전에 이 지시부터 다시 물어보세요.

이것 하나가 남은 이유는 이것이 경고가 아니라 **관문**이기 때문입니다. ``[[결제 링크 URL]]``(운영자가 채울
자리), 치환 안 된 ``{{MEETING_LINK}}``, ``[Name]``·``[이름]``·``{고객 이름}`` 같은 빈칸은 고객에게 가면
되돌릴 수 없습니다. 그래서 승인이 거절하고(``approval.approve``), 발송이 한 번 더 막습니다
(``senders.enforce_no_unfilled_slots``). 발송에도 그물이 있어야 하는 것은 후속 리마인더 때문입니다 —
그 행은 사람이 아니라 코드가 승인합니다(``followup_sequence``).

결정적이고, 모델을 부르지 않고, 본문을 고치지 않습니다 — 찾아서 그 줄을 짚을 뿐입니다.
"""

from __future__ import annotations

import re
from bisect import bisect_right

# 서식·예시 문서가 빈칸에 쓰는 말. **목록으로 좁힙니다** — 막는 검사라서 「[안내]」·「[EN]」 같은
# 멀쩡한 글자를 잡으면 운영자가 빠져나갈 길이 없습니다. 콘솔 예시(Template A~G)의 빈칸이 기준입니다.
_SLOT_WORDS = (
    r"(?:(?:customer|client|contact|recipient|your|first|last|full|company|sender)\s+)?"
    r"(?:name|company(?:\s+name)?|title|e-?mail(?:\s+address)?|phone(?:\s+number)?|date|amount|price|number)"
    # 링크 빈칸은 서식이 실제로 쓰는 이름만 — 「[Share Link]」 같은 버튼 이름은 멀쩡한 글자입니다.
    r"|(?:(?:pricing\s*/\s*)?sign-?\s?up|pricing|payment|checkout|booking|meeting|calendly|download)?\s*(?:link|url)"
    r"|이름|성함|고객\s*(?:이름|명)|회사\s*(?:이름|명)|담당자(?:\s*이름|\s*명|명)?|업무\s*이메일|이메일"
    r"|연락처|날짜|금액|링크|결제\s*링크"
    r"|[^\[\]\n]*(?:산출|확인\s*필요|§)[^\[\]\n]*"
    r"|TBD|TODO|FIXME|placeholder"
)
_PLACEHOLDERS = (
    re.compile(r"\[\[[^\[\]]{1,200}\]\]"),                       # [[결제 링크]] — 운영자가 채울 자리
    re.compile(r"\{\{[^{}]{1,80}\}\}"),                          # {{MEETING_LINK}} — 치환 안 된 토큰
    # {고객 이름}, {name} — 주소·코드 속(`/v1/voices/{voice_id}`, `${name}`, 백틱)은 API 안내라 뺍니다.
    re.compile(r"(?<![{/`$\w])\{\s*[^\W\d][\w ]{0,30}\}(?![}`])"),
    re.compile(rf"(?<!\[)\[\s*(?:{_SLOT_WORDS})\s*\](?![(\[\]])", re.IGNORECASE),  # [Name], [이름]
    re.compile(r"(?<!\[)\[[A-Z]\](?![(\[\]])"),                  # [N], $[P] — 숫자를 넣을 자리
)
# 한국어가 아닌 메일 속 대괄호 한국어는 쓰는 사람에게 남긴 지시입니다(「[가격을 먼저 안내했다면]」
# 같은 것). 한국어 메일에서는 「[안내]」 같은 머리말일 수 있어 안 봅니다.
_KOREAN_IN_BRACKETS = re.compile(r"(?<!\[)\[[^\[\]\n]*[가-힣][^\[\]\n]*\](?![(\[\]])")


def _lines_at(text: str, spans: list[tuple[int, int]]) -> list[str]:
    """걸린 자리가 든 줄들 — 여러 줄에 걸친 자리(``[[Stripe\\nlink]]``)는 그 줄 전부."""
    lines = text.split("\n")
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    picked: set[int] = set()
    for begin, end in spans:
        first = bisect_right(starts, begin) - 1
        last = bisect_right(starts, max(begin, end - 1)) - 1
        picked.update(range(first, last + 1))
    return [lines[index].strip() for index in sorted(picked) if lines[index].strip()]


def unfilled_slots(body: str | None, *, language: str | None = None) -> list[str]:
    """``body`` 에서 채우지 않은 자리가 든 줄들(앞뒤 공백을 뺀 글자 그대로). 없으면 빈 목록.

    ``language`` 는 나갈 언어입니다 — 한국어가 아닌 메일이면 대괄호 속 한국어도 빈칸으로 봅니다
    (모르면 안 봅니다).
    """
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    patterns = list(_PLACEHOLDERS)
    if language and not language.lower().startswith("ko"):
        patterns.append(_KOREAN_IN_BRACKETS)
    return _lines_at(text, [match.span() for pattern in patterns for match in pattern.finditer(text)])


def quote_lines(lines: list[str], *, limit: int = 3) -> str:
    """운영자가 그 줄을 찾을 수 있게 — 「…」 로 앞의 ``limit`` 줄까지, 줄마다 80자까지."""
    shown = ", ".join(f"「{line[:80]}{'…' if len(line) > 80 else ''}」" for line in lines[:limit])
    return shown + (f" 외 {len(lines) - limit}줄" if len(lines) > limit else "")
