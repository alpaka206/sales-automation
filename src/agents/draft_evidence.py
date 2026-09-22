"""Limited deterministic checks, not a semantic policy correctness certificate."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field


class PolicyQuote(BaseModel):
    source_id: int
    quote: str


class AnswerPoint(BaseModel):
    """Short answer obligations, not verified facts or policy decisions."""

    question: str
    supported_answer: str = Field(min_length=1)
    verification_needed: str | None = None


def compose_answer(points: list[AnswerPoint]) -> str:
    """Preserve generated answers instead of asking a second rendering to omit them."""
    paragraphs = []
    for point in points:
        for text in (point.supported_answer, point.verification_needed):
            if text and text.strip() and text.strip() not in paragraphs:
                paragraphs.append(text.strip())
    if not paragraphs:
        raise DraftEvidenceError("초안 답변 요소가 비어 있습니다.")
    return "\n\n".join(paragraphs)


class DraftEvidenceError(RuntimeError):
    pass


def _plain(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


_DURATION = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?(?:\s*[~–-]\s*\d+(?:\.\d+)?)?)"
    r"\s*(?:영업일|business\s+days?|days?|weeks?|months?|hours?|minutes?|일|주|개월|시간|분)",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"(?:환불|삭제|신청|요청|승인|확인\s*절차).{0,45}?(?:완료(?:되었|됐|했)|접수(?:되었|됐|했)|진행\s*중|처리\s*중|진행하고\s*있)"
    r"|(?:환불|다운로드|결제|사용\s*기록|계약|계정\s*기록).{0,45}?(?:확인하고\s*있|조회하고\s*있|검토\s*중|확인\s*중)"
    r"|(?:refund|deletion|request).{0,45}?(?:has been|is being|was)\s+(?:processed|completed|approved|submitted)",
    re.IGNORECASE,
)
_NEGATIVE_OR_CONDITIONAL = re.compile(
    r"않|아니|못|확인할\s*수\s*없|완료되면|완료된\s*후|완료\s*후|(?:\bnot\b|\bcannot\b|\bwhether\b|\bif\b)",
    re.IGNORECASE,
)
_UNVERIFIED_NEGATIVE = re.compile(
    r"(?:환불|삭제).{0,40}?(?:완료|처리).{0,16}?(?:아니|아닙|않)"
    r"|(?:refund|deletion).{0,40}?(?:has not been|was not|is not)\s+(?:completed|processed)",
    re.IGNORECASE,
)
_EXPLICIT_UNKNOWN = re.compile(r"확인할\s*수\s*없|알\s*수\s*없|인지|여부|\bwhether\b|\bcannot\b", re.IGNORECASE)


def _duration_key(value: str) -> tuple:
    numbers = tuple(float(n) for n in re.findall(r"\d+(?:\.\d+)?", value))
    unit = re.sub(r"[\d.\s~–-]", "", value).lower()
    if unit in {"일", "day", "days"}:
        return numbers, "day"
    if unit in {"주", "week", "weeks"}:
        return tuple(n * 7 for n in numbers), "day"
    if unit in {"영업일", "businessday", "businessdays"}:
        return numbers, "business_day"
    if unit in {"시간", "hour", "hours"}:
        return tuple(n * 60 for n in numbers), "minute"
    if unit in {"분", "minute", "minutes"}:
        return numbers, "minute"
    return numbers, "month"


def check_draft(body: str, quotes: list[PolicyQuote], *, documents, customer_text: str) -> list[str]:
    """Check quote membership, novel durations and unsupported execution claims.

    Matching quotes do not prove entailment or coverage. Customer-provided durations
    may be restated, never promoted to verified facts by this check.
    """
    issues = []
    allowed = {doc.id: doc.body or "" for doc in documents}
    for quote in quotes:
        if quote.source_id not in allowed or not quote.quote.strip() or _plain(quote.quote) not in _plain(allowed[quote.source_id]):
            issues.append("invalid_policy_quote")
    source_text = "\n".join(allowed.values()) + "\n" + customer_text
    supported = {_duration_key(match.group()) for match in _DURATION.finditer(source_text)}
    for match in _DURATION.finditer(body):
        if _duration_key(match.group()) not in supported:
            issues.append("unsupported_duration")
    # This drafting path has no action execution receipts. Avoid pretending one exists.
    for sentence in re.split(r"[\n.!?]+", body):
        # No receipt means UNKNOWN, not "not completed". Do not invert uncertainty.
        if _UNVERIFIED_NEGATIVE.search(sentence) and not _EXPLICIT_UNKNOWN.search(sentence):
            issues.append("unsupported_execution_status")
        for action in _ACTION.finditer(sentence):
            # A later, separate negative clause cannot excuse an affirmative status:
            # "환불 진행 중이며, 아직 완료되지 않았습니다" still invents progress.
            tail = re.split(r",|이며|지만|그리고", sentence[action.end():], maxsplit=1)[0]
            if _NEGATIVE_OR_CONDITIONAL.search(tail):
                continue
            issues.append("unsupported_execution_status")
    return sorted(set(issues))


def repair_instruction(issues: list[str]) -> str:
    return (
        "이전 생성 결과의 검증 실패: " + ", ".join(issues) + ". "
        "같은 원문을 사용해 답변을 다시 작성하세요. 원문/고객 발화에 없는 기간이나 UI 절차를 만들지 마세요. "
        "이 경로는 조회·환불·삭제·접수를 실행하지 않습니다. 진행 중/접수됨/완료됐다고 하지 말고 "
        "확인이 필요한 사실과 일반 절차를 구분하세요. 확인이 필요해도 원문으로 답할 수 있는 부분은 답하세요. "
        "인용은 제공된 source_id와 해당 원문의 정확한 연속 구절만 사용하세요."
    )
