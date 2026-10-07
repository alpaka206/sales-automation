"""Limited deterministic checks, not a semantic policy correctness certificate."""
from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel


class PolicyQuote(BaseModel):
    source_id: int
    quote: str


class AnswerPoint(BaseModel):
    """질문별 점검 기록 — 본문이 아닙니다. 본문은 모델이 쓴 글 그대로 나갑니다(2026-10-06).

    예전에는 이 목록을 코드가 이어 붙여 본문으로 썼습니다(`compose_answer`, 2026-09-21 ADR). 조건이 빠지는
    길을 막으려던 것인데, 인사·링크·맺음이 엉뚱한 자리에 섰습니다(평가 F415a: 확인 문장이 「감사합니다.」와
    미팅 링크 뒤에 붙었다). 이제 여기 적힌 숫자가 본문에서 빠졌는지만 봅니다(`answer_point_gaps`). 점검용이라
    칸이 비어도 초안 전체가 스키마에서 떨어지지 않게 전부 기본값이 있습니다.
    """

    question: str = ""
    supported_answer: str = ""
    verification_needed: str | None = None


class DraftEvidenceError(RuntimeError):
    """보여 줄 본문이 없는 초안 — 이것만 큐에서 재시도 없이 끝납니다(`inbound_worker`)."""


# 인용 대조에서 빼는 것: 공백 · 마크다운 강조 · 표 구분자(| · 탭 · ---). 모델은 노션의 탭 표를 | 로, 굵은
# 글씨를 ** 없이 옮겨 적습니다 — 맞는 인용이 표 모양 하나로 invalid 였습니다(평가 F415d 두 번).
_QUOTE_NOISE = re.compile(r"-{3,}|[\s*_`|#>]+")
_QUOTE_MARKS = str.maketrans("‘’“”", "''\"\"")


def _plain(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").translate(_QUOTE_MARKS)
    return _QUOTE_NOISE.sub("", text).lower()


# 숫자 하나 — 천 단위 쉼표 포함. 「12,000 minutes」를 「000 minutes」로 읽던 것을 고쳤습니다.
_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_NUMBER = re.compile(rf"(?<![\d.,]){_NUM}")
_DURATION = re.compile(
    rf"(?<![\d.,])({_NUM}(?:\s*[~–-]\s*{_NUM})?)"
    r"\s*(?:영업일|business\s+days?|days?|weeks?|months?|hours?|minutes?|일|주|개월|시간|분)",
    re.IGNORECASE,
)
# 날짜는 기간이 아니다 — 「2026년 9월 21일」의 「21일」이 없는 기간으로 잡히면 안 된다.
_CALENDAR_DATE = re.compile(r"\d{4}\s*년|\d{1,2}\s*월\s*\d{1,2}\s*일")
# 이 검사가 잡으려는 것은 지어낸 처리 기간이다. 미팅 길이·플랜 분수처럼 절차와 무관한 문장의
# 숫자까지 원문에서 찾으면 멀쩡한 첫 회신이 재작성 한 번 뒤 draft_failed 로 죽는다.
_DURATION_CONTEXT = re.compile(
    r"환불|처리|소요|절차|접수|삭제|반영|배송|이내|안에|걸[리립려]|refund|process|take|within|deliver|complet",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"(?:환불|삭제|신청|요청|승인|확인\s*절차).{0,45}?(?:완료(?:되었|됐|했)|접수(?:되었|됐|했)|진행\s*중|처리\s*중|진행하고\s*있)"
    r"|(?:환불|다운로드|결제|사용\s*기록|계약|계정\s*기록).{0,45}?(?:확인하고\s*있|조회하고\s*있|검토\s*중|확인\s*중)"
    r"|(?:refund|deletion|request).{0,45}?(?:has been|is being|was)\s+(?:processed|completed|approved|submitted)",
    re.IGNORECASE,
)
_NEGATIVE_OR_CONDITIONAL = re.compile(
    r"않|아니|못|확인할\s*수\s*없|완료되면|완료된\s*후|완료\s*후|는지|여부|확인이\s*필요|필요합니다"
    r"|(?:\bnot\b|\bcannot\b|\bwhether\b|\bif\b|need(?:s)?\s+to\s+(?:be\s+)?(?:confirm|verif))",
    re.IGNORECASE,
)
_UNVERIFIED_NEGATIVE = re.compile(
    r"(?:환불|삭제).{0,40}?(?:완료|처리).{0,16}?(?:아니|아닙|않)"
    r"|(?:refund|deletion).{0,40}?(?:has not been|was not|is not)\s+(?:completed|processed)",
    re.IGNORECASE,
)
_EXPLICIT_UNKNOWN = re.compile(r"확인할\s*수\s*없|알\s*수\s*없|인지|여부|\bwhether\b|\bcannot\b", re.IGNORECASE)


def _numbers(text: str | None) -> set[float]:
    return {float(found.replace(",", "")) for found in _NUMBER.findall(text or "")}


def _duration_key(value: str) -> tuple:
    numbers = tuple(float(n.replace(",", "")) for n in re.findall(_NUM, value))
    unit = re.sub(r"[\d.,\s~–-]", "", value).lower()
    if unit in {"일", "day", "days"}:
        return numbers, "day"
    if unit in {"주", "week", "weeks"}:
        return tuple(n * 7 for n in numbers), "day"
    if unit in {"영업일", "businessday", "businessdays"}:
        # 영업일도 「일」로 접는다. 달력 의미를 인증하는 검사가 아니라 지어낸 기간을 잡는 검사라서다 —
        # 국문 문서의 「영업일 3일」과 영문 초안의 "3 business days" 는 ensure_language 를 지난 같은 문장인데,
        # 여기서 갈리면 변환 뒤 검사에서 죽고 그 자리에는 재작성이 없다.
        return numbers, "day"
    if unit in {"시간", "hour", "hours"}:
        return tuple(n * 60 for n in numbers), "minute"
    if unit in {"분", "minute", "minutes"}:
        return numbers, "minute"
    return numbers, "month"


def check_draft(body: str, quotes: list[PolicyQuote], *, documents, customer_text: str) -> list[str]:
    """Check quote membership, novel durations and unsupported execution claims.

    Matching quotes do not prove entailment or coverage. Customer-provided durations
    may be restated, never promoted to verified facts by this check. A quote counts when
    its words are in **any** document the draft saw — the model cites a real sentence under
    the neighbouring document's id often enough (evaluation R424) that the id is not evidence.
    """
    issues = []
    allowed = {doc.id: doc.body or "" for doc in documents}
    plain_docs = [_plain(text) for text in allowed.values()]
    for quote in quotes:
        wanted = _plain(quote.quote)
        if not wanted or not any(wanted in text for text in plain_docs):
            issues.append("invalid_policy_quote")
    source_text = _CALENDAR_DATE.sub(" ", "\n".join(allowed.values()) + "\n" + customer_text)
    supported = {_duration_key(match.group()) for match in _DURATION.finditer(source_text)}
    for sentence in re.split(r"[\n.!?]+", _CALENDAR_DATE.sub(" ", body)):
        if not _DURATION_CONTEXT.search(sentence):
            continue
        for match in _DURATION.finditer(sentence):
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


def answer_point_gaps(body: str, points: list[AnswerPoint]) -> list[str]:
    """``answer_points`` 에 적은 숫자(금액 · 비율 · 기간 · 수량)가 본문에 없으면 ``answer_point_missing``.

    모델이 질문별로 「이렇게 답했다」고 적은 것과 실제 본문을 숫자로만 맞대 봅니다 — 문장은 언어·표현이
    달라 맞댈 수 없고, 빠지면 아픈 것은 「14일 이내」 같은 조건의 숫자입니다. 재작성 한 번을 부르고
    매니페스트에 남을 뿐 본문은 안 고칩니다. 목록 번호(「1. 」)는 숫자로 안 셉니다.
    """
    have = _numbers(body)
    for point in points:
        text = re.sub(r"^\s*\d{1,2}[.)]\s+", "", point.supported_answer or "")
        if _numbers(text) - have:
            return ["answer_point_missing"]
    return []


def repair_instruction(issues: list[str], *, problems=(), previous: str = "") -> str:
    """재작성 한 번에 실을 말 — 문자열 검사(``issues``)와 보내기 전 검토(``problems``)가 찾은 것.

    ``previous`` 는 직전 초안입니다. 같이 주어야 모델이 지적된 곳만 고치고 나머지(맞게 쓴 계산 · 질문)를 살립니다 —
    없으면 처음부터 다시 쓰다가 멀쩡하던 곳에서 새 실수를 냅니다.
    """
    parts: list[str] = []
    if issues:
        text = (
            "이전 생성 결과의 검증 실패: " + ", ".join(issues) + ". "
            "같은 원문을 사용해 답변을 다시 작성하세요. 원문/고객 발화에 없는 기간이나 UI 절차를 만들지 마세요. "
            "이 경로는 조회·환불·삭제·접수를 실행하지 않습니다. 진행 중/접수됨/완료됐다고 하지 말고 "
            "확인이 필요한 사실과 일반 절차를 구분하세요. 확인이 필요해도 원문으로 답할 수 있는 부분은 답하세요. "
            "인용은 제공된 source_id와 해당 원문의 정확한 연속 구절만 사용하세요."
        )
        if "answer_point_missing" in issues:
            text += " answer_points 에 적은 숫자·조건은 body 에도 그대로 들어가야 합니다."
        parts.append(text)
    if problems:
        lines = [
            (f"- 「{problem.quote}」: " if problem.quote else "- ") + problem.problem
            + (f" → {problem.fix}" if problem.fix else "")
            for problem in problems
        ]
        parts.append("보내기 전 검토에서 찾은 문제입니다. 회사 문서와 대화를 다시 확인해 고치세요:\n" + "\n".join(lines))
    if previous:
        parts.append(f'직전 초안입니다. 위의 문제만 고치고 나머지는 살립니다:\n"""\n{previous}\n"""')
    return "\n\n".join(parts)
