"""콘솔 문서를 구간으로 나누고 꼬리표를 답니다 — 초안은 기밀 구간만 뺀 전부를 종류별로 묶어 읽습니다.

운영자가 콘솔(정책 문서 · 이메일 템플릿)에 쓰는 글은 모양이 제각각입니다. 마크다운 제목이
있기도, 노션에서 붙여넣은 탭 표이기도, 정리 안 된 메모 한 덩어리이기도 합니다. 그것을
통째로 「Company rules (must follow)」로 실으면 템플릿·완성 예시·무응답 리마인드 골격·원가가
든 내부 단가표까지 전부 「반드시 따를 규칙」이 됩니다(2026-10-06 평가: 초안마다 39~46천 자).

두 단계입니다.

1. **나누기는 코드가 합니다** (``split_sections``, 모델 없음). 구간은 본문의 **오프셋**이라
   글자를 바꾸지 않습니다 — 초안의 인용 검사가 원문과 대조하므로, 다시 쓴 요약은 매번 인용
   실패가 되고 「않는다」 하나가 빠져도 아무도 모릅니다. 구간을 이어 붙이면 본문이 되어야
   하고, 안 되면 본문 통째로 한 구간입니다.
2. **꼬리표는 모델이 답니다** (``policy/organize``, flash 한 번). 무슨 종류인가 · 어느 상황에
   쓰는가 · 고객 메일에 나가면 안 되는 문자열은 무엇인가.

**모델의 꼬리표보다 금지 목록이 이깁니다** (``_vocabulary``). 표시된 줄(비공개 · ❌ ·
⛔ · 원가 · 이익률 …)의 금액과 공급사 이름은 코드가 직접 뽑고, 초안에 싣는 구간에서는 그
문자열을 지웁니다. 「사람만 본다」 문서와 서명은 모델에 안 갑니다 — 나누기와 금지 목록
추출만 코드가 합니다.

지도는 본문 해시와 ``ORGANIZER_VERSION`` 에 묶입니다. 본문이 바뀌면 낡은 지도가 되고, 그
문서는 다시 정리될 때까지 예전처럼 **본문 통째로** 갑니다(``knowledge_for`` 의 ``full``).
지도는 ``PolicyDocument`` 에 안 들어갑니다 — 거기 들어가면 지문이 바뀌어 대기 중인 초안이
전부 「정책이 변경되었습니다」로 막힙니다. 원본 행의 판 번호도 안 건드립니다.

정리는 요청 안에서 하지 않습니다. 10분 폴러가 회차마다 낡은 문서 **하나**를 하고
(``organize_pending``), 콘솔에서 문서를 저장하면 뒤에서 바로 한 번 합니다(``backfill`` —
``policy_docs.schedule_reorganize``).
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import BaseModel

from ..db.email_templates import SIGNATURE_KEY_PREFIX
from ..db.models import EmailTemplate, Event, PolicySection, PolicySource
from .policy_context import PolicyContextError, fingerprint

logger = logging.getLogger(__name__)

# 나누는 법이나 꼬리표 프롬프트를 바꾸면 올립니다. 지도가 전부 낡은 것이 되고 폴러가 회차마다
# 하나씩 다시 정리합니다 — 그동안 그 문서들은 예전처럼 본문 통째로 갑니다.
ORGANIZER_VERSION = 3  # 2: 견적 단가 구간은 internal_price · 3: applies_to 는 「문서가 한정한 회신」만

POLICY = "policy"
TEMPLATE = "template"

Kind = Literal[
    "rule", "format", "example", "template", "fact", "public_price",
    "internal_price", "confidential", "process", "other",
]
Situation = Literal["first_reply", "answer_reply", "nudge", "closing_notice", "any"]
KINDS: tuple[str, ...] = get_args(Kind)
ANY = "any"
SITUATIONS: tuple[str, ...] = tuple(value for value in get_args(Situation) if value != ANY)

# 꼬리표 한 번의 출력 한도. 생각 토큰이 이 **안에서** 쓰입니다(`tests/test_llm_budgets.py`) —
# 구간 50개면 꼬리표 JSON 만 4~5천 토큰이라, 빠듯하게 두면 JSON 이 잘려 실패합니다.
LABEL_MAX_TOKENS = 16000
# 이보다 긴 본문은 모델에 안 보냅니다(실패로 남고, 그동안 본문 통째로 갑니다).
_MAX_BODY_CHARS = 120_000
# 폴러가 같은 본문에 대해 저절로 다시 해 보는 횟수. 넘으면 문서를 다시 저장하거나(저장 직후의 정리는
# 이 횟수를 안 봅니다) 본문이 바뀔 때까지 기다립니다 — 계속 실패하는 문서가 10분마다 모델을 부르지 않게.
_MAX_AUTO_ATTEMPTS = 3
FAILED_EVENT = "policy_organize_failed"

# 나누기의 크기. 제목이 없는 메모는 빈 줄에서 이만큼씩 묶고, 제목 구간이라도 너무 크면 같은
# 규칙으로 한 번 더 나눕니다. 코드 블록(완성 예시)과 표는 빈 줄이 없으니 쪼개지지 않습니다.
_MIN_GROUP = 1200
_MAX_GROUP = 2500
_MAX_SECTION = 3000

REDACTED = "[비공개]"

# 이 종류의 구간은 금액을 그대로 둡니다. 나머지(규칙·사실·형식·기타)에서는 문서가 내부 단가·기밀
# 구간에 적은 금액을 지웁니다 — 「단가 바닥: …」처럼 규칙에 되풀이된 내부 숫자가 모든 회신에 실리지
# 않게. 그 밖의 금액(규칙에 적힌 환불 기준 같은 것)은 남습니다.
_MONEY_KINDS = frozenset({"public_price", "internal_price", "example", "template"})
# 고객에게 실제로 하는 말 — 금지 목록 후보가 이 안에 있으면 모델의 착오로 봅니다.
_PUBLIC_KINDS = frozenset({"public_price", "example", "template"})
# 초안에 절대 안 싣는 종류 — 기밀뿐입니다. **꼬리표로 내용을 빼지 않습니다** (2026-10-06 평가): 「내부 절차」로
# 달린 판정 문서의 에스컬레이션 조건(「이 경우 회신에 상업 조건을 쓰지 않는다」)이 모든 초안에서 사라졌습니다.
_NEVER = frozenset({"confidential"})

# 렌더링 순서와 머리말. 종류의 뜻을 말할 뿐 업무 규칙이 아닙니다 — 규칙은 문서 안에 있습니다.
_GROUPS = (
    ("rule", "회사 규칙 — 반드시 따릅니다"),
    ("format", "회신 형식"),
    ("template", "회신 템플릿 (골격)"),
    ("example", "완성 회신 예시"),
    ("fact", "제품 사실"),
    ("public_price", "공개 가격 — 고객에게 안내할 수 있습니다"),
    ("other", "기타 참고"),
    (
        "internal_price",
        "견적 계산용 내부 단가 — 이번 고객의 견적을 계산하는 데만 씁니다. "
        "표·범위·하한·원가로 고객에게 보여 주지 않습니다",
    ),
    ("process", "내부 절차 — 판단에 참고하고, 절차 자체를 고객에게 설명하지 않습니다"),
)

# 구간 머리에 붙이는 「…에만 적용」 — 문서가 그 회신으로 한정한 구간입니다. 거르지 않고 알려 줍니다(`_fits`).
_SITUATION_NAMES = {
    "first_reply": "첫 회신",
    "answer_reply": "고객 답장에 대한 회신",
    "nudge": "답이 없을 때 다시 보내는 메일",
    "closing_notice": "종료 안내",
}

# 값만 든 템플릿 행 — 코드가 토큰 자리에 그대로 끼우는 링크와 이름입니다(`prompts._EDITABLE_TOKENS`).
# 글이 아니라 정리할 것이 없고, 서명처럼 사람 이름이 들어 있어 모델에 보낼 이유도 없습니다.
_VALUE_KEYS = frozenset({"meeting_link", "whatsapp_link", "sender_name"})

# 금지 목록에 두면 평범한 메일까지 막는 낱말 — 「two business days」가 막히면 안 됩니다.
_GENERIC_TERMS = frozenset({
    "perso", "perso ai", "perso dubbing", "dubbing", "enterprise", "business", "pro", "creator",
    "starter", "free", "plus", "core", "scale", "standard", "basic", "premium", "api", "sso",
    "plan", "plans", "credit", "credits", "lip-sync", "lipsync", "voice-only", "po", "finance",
    "더빙", "립싱크", "크레딧", "플랜", "요금", "단가", "가격",
})


class OrganizerError(RuntimeError):
    """정리가 끝까지 안 됐다 — 그 문서는 예전처럼 본문 통째로 갑니다."""


# ---------------------------------------------------------------- 나누기 (모델 없음)

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_MD_HEADING_RE = re.compile(r"^ {0,3}(#{1,4}) +\S")
_SECTION_SIGN_RE = re.compile(r"^ {0,3}§ ?[0-9A-Za-z]")
_STEP_RE = re.compile(r"^ {0,3}\d{1,2} ?단계(?:[.):]|\s|$)")
_SUBNUMBER_RE = re.compile(r"^ {0,3}(\d{1,2}(?:-\d{1,2}){1,3})[.)]? +\S")


def _clean_heading(line: str) -> str:
    text = re.sub(r"^\s*(?:#+\s*)+", "", line.strip())  # 「# # 제목」 — 라벨이 #로 시작하던 행
    text = re.sub(r"\s+#+\s*$", "", text)
    return text.replace("**", "").replace("`", "").strip()[:200]


def _heading(line: str) -> tuple[int, str] | None:
    """제목 줄이면 (깊이, 글자). 탭이 든 줄은 노션 표의 한 행이라 제목이 아닙니다."""
    if "\t" in line:
        return None
    found = _MD_HEADING_RE.match(line)
    if found and len(line) <= 200:
        return len(found.group(1)), _clean_heading(line)
    if len(line.strip()) > 100:  # 길면 번호로 시작하는 문장입니다
        return None
    if _SECTION_SIGN_RE.match(line):
        return 2, _clean_heading(line)
    if _STEP_RE.match(line):
        return 3, _clean_heading(line)
    found = _SUBNUMBER_RE.match(line)
    if found:
        return 2 + found.group(1).count("-"), _clean_heading(line)
    return None


def _scan(body: str):
    """제목 줄과, 빈 줄 다음에 시작하는 문단의 오프셋. 코드 블록 안은 둘 다 아닙니다."""
    heads: list[tuple[int, int, int, str]] = []
    cuts: list[int] = []
    offset, fence, previous_blank = 0, "", True
    for line in body.splitlines(keepends=True):
        text = line.strip()
        if fence:
            if text.startswith(fence):
                fence = ""
        else:
            if text and previous_blank:
                cuts.append(offset)
            found = _heading(line.rstrip("\r\n"))
            if found:
                heads.append((offset, offset + len(line), *found))
            else:
                opened = _FENCE_RE.match(line)
                if opened:
                    fence = opened.group(1)
        previous_blank = not text
        offset += len(line)
    return heads, cuts


def _has_content(text: str) -> bool:
    return any(line.strip(" \t\r-*_=") for line in text.splitlines())


def _heading_pieces(body: str, heads) -> list[tuple[int, int, str]]:
    pieces: list[list] = []
    if not heads or heads[0][0] > 0:
        pieces.append([0, 0, "", 0])
    stack: list[tuple[int, str]] = []
    for start, line_end, level, title in heads:
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        pieces.append([start, 0, " › ".join(name for _level, name in stack if name), line_end])
    for index, piece in enumerate(pieces):
        piece[1] = pieces[index + 1][0] if index + 1 < len(pieces) else len(body)

    # 제목만 있고 내용이 없는 조각(「## §3. 회신 템플릿」 바로 밑에 「### Template A」)은 다음
    # 조각의 앞머리가 됩니다 — 제목 경로에 이미 그 이름이 들어 있습니다.
    merged: list[tuple[int, int, str]] = []
    carry: int | None = None
    for start, end, path, head_end in pieces:
        if carry is not None:
            start, carry = carry, None
        if not _has_content(body[max(start, head_end):end]):
            carry = start
            continue
        merged.append((start, end, path))
    if carry is not None:
        if merged:
            first, _end, path = merged[-1]
            merged[-1] = (first, len(body), path)
        else:
            merged.append((carry, len(body), ""))
    return merged


def _paragraph_groups(start: int, end: int, cuts: list[int]) -> list[tuple[int, int]]:
    """빈 줄에서 1,200~2,500자씩. 빈 줄이 없는 덩어리(코드 블록·표)는 통째로 남습니다."""
    points = [cut for cut in cuts if start < cut < end]
    groups: list[tuple[int, int]] = []
    current = start
    for index, point in enumerate(points):
        following = points[index + 1] if index + 1 < len(points) else end
        size = point - current
        if size >= _MAX_GROUP or (size >= _MIN_GROUP and following - current > _MAX_GROUP):
            groups.append((current, point))
            current = point
    groups.append((current, end))
    if len(groups) > 1 and groups[-1][1] - groups[-1][0] < _MIN_GROUP // 2:
        tail = groups.pop()
        groups[-1] = (groups[-1][0], tail[1])
    return groups


def _covers(spans, length: int) -> bool:
    position = 0
    for start, end, *_rest in spans:
        if start != position or end <= start:
            return False
        position = end
    return position == length


def split_sections(body: str | None) -> list[tuple[int, int, str]]:
    """본문을 (시작, 끝, 제목 경로) 구간으로. 이어 붙이면 본문 그대로입니다.

    경계는 마크다운 제목(# ~ ####, 「# # 」 두 겹 포함) · 「§1.」 · 「3단계.」 · 「4-1.」 줄입니다.
    코드 블록과 표 안은 경계가 아닙니다. 경계가 하나도 없는 메모는 빈 줄에서 묶습니다.
    """
    body = body or ""
    if not body.strip():
        return []
    heads, cuts = _scan(body)
    spans: list[tuple[int, int, str]] = []
    for start, end, path in _heading_pieces(body, heads):
        if end - start > _MAX_SECTION:
            spans.extend((first, last, path) for first, last in _paragraph_groups(start, end, cuts))
        else:
            spans.append((start, end, path))
    if not _covers(spans, len(body)):
        logger.warning("구간이 본문을 빈틈없이 덮지 못해 본문 통째로 한 구간으로 둡니다.")
        return [(0, len(body), "")]
    return spans


# ---------------------------------------------------------------- 금지 목록 (모델 없음)

_MONEY = (
    r"(?:US\$|[$€£¥₩])\s?\d[\d,]*(?:\.\d+)?"
    r"|(?:USD|KRW|EUR|JPY)\s?\d[\d,]*(?:\.\d+)?"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:USD|KRW|EUR|JPY)(?![A-Za-z])"
    # 원·달러는 숫자에 붙어 있을 때만 — 「§2 원가」가 「2 원」이 되지 않게.
    r"|\d[\d,]*(?:\.\d+)?(?:원(?!가|칙)|달러)"
)
_MONEY_RE = re.compile(_MONEY)
_AMOUNT_RE = re.compile(_MONEY + r"|(?<![\d.])\d+(?:\.\d+)?\s?%")
# 이 표시가 붙은 문장·칸의 금액·비율은 문서 스스로 대외 금지라고 말한 숫자입니다.
_MARKER_RE = re.compile(
    r"비공개|❌|⛔|내부|원가|이익률|마진|승인\s*하한|confidential|internal only|do not share|margin",
    re.IGNORECASE,
)
# ❌·⛔ 로 **시작하는** 줄은 줄 전체가 그 금지의 내용입니다(「❌ … 말하지 말 것. 경쟁사 $0.12 …」).
_LEADING_MARK_RE = re.compile(r"^[\s>*\-•]*(?:❌|⛔)")
# 문장 · 「;」 · 띄어 쓴 「 · 」·「 — 」 로 나눈 마디. 「불일치($0.99 vs $1.99) · §2 원가 각주」에서
# 원가는 뒷마디의 말입니다.
_CLAUSE_RE = re.compile(r"(?<=[.!?。])\s+|;\s*|\s·\s|\s[—–]\s")
# 표의 머리 칸이 이것이면 그 **열**의 숫자가 원가·마진입니다(같은 표의 판매 단가 열은 아닙니다).
_COST_COLUMN_RE = re.compile(r"원가|이익률|마진|margin", re.IGNORECASE)
_SUPPLIER_RE = re.compile(r"공급사|공급\s*관계|벤더|supplier|vendor", re.IGNORECASE)
_PAREN_NAME_RE = re.compile(r"\(([A-Z][A-Za-z0-9.\-]{1,30})(?:\s+V?\d[\w.]*)?\)")
_TABLE_RULE_RE = re.compile(r"^[\s|:\-]+$")


def _cells(line: str) -> list[str] | None:
    if line.lstrip().startswith("|"):
        return [cell.strip() for cell in line.strip().strip("|").split("|")]
    if "\t" in line:
        return [cell.strip() for cell in line.split("\t")]
    return None


def _amounts(text: str) -> set[str]:
    return {found.group(0) for found in _AMOUNT_RE.finditer(text)}


def _marked_amounts(text: str) -> set[str]:
    """표시가 붙은 **마디**의 금액만. 개정 메모의 「§2 원가 각주」 · 「⛔ 재조사 금지」가 같은 줄의
    공개 가격까지 금지로 만들면 안 됩니다(운영 문서에서 실제로 그랬습니다 — 공개 탑업 단가 둘).
    그 공개 가격이 금지 목록에 들면 그 가격을 안내하는 회신이 승인에서 막힙니다."""
    if _LEADING_MARK_RE.match(text):
        return _amounts(text)
    return {
        amount
        for clause in _CLAUSE_RE.split(text) if _MARKER_RE.search(clause)
        for amount in _amounts(clause)
    }


def deterministic_terms(text: str | None) -> set[str]:
    """문서가 스스로 표시한 것만 — 모델 없이. 「사람만 본다」 문서도 여기는 지납니다.

    * 비공개 · ❌ · ⛔ · 내부 · 원가 · 이익률 · 마진 · 승인 하한 이 붙은 문장(표에서는 칸,
      첫 칸이면 그 행 전체)의 금액과 비율. ❌ · ⛔ 로 시작하는 줄은 줄 전체.
    * 머리 칸이 원가 · 이익률 · 마진인 표의 그 **열**의 금액과 비율(같은 표의 판매 단가 열은 아님)
    * 공급사 · 벤더를 말하는 줄에서 괄호 안의 이름(「음성 엔진(VoxEngine V3)」)

    금액은 통화 기호·단위가 붙은 것과 %만 셉니다. 「300분」 같은 분량은 고객이 쓴 말과 겹쳐서
    금지 목록에 두면 멀쩡한 답장이 막힙니다.
    """
    terms: set[str] = set()
    cost_columns: list[int] | None = None  # None — 표 밖
    for line in (text or "").splitlines():
        cells = _cells(line)
        if cells is None:
            cost_columns = None
            if _MARKER_RE.search(line):
                terms |= _marked_amounts(line)
        else:
            if cost_columns is None:
                cost_columns = [index for index, cell in enumerate(cells) if _COST_COLUMN_RE.search(cell)]
            elif not _TABLE_RULE_RE.match(line):
                for index in cost_columns:
                    if index < len(cells):
                        terms |= _amounts(cells[index])
            if _MARKER_RE.search(cells[0]):
                terms |= _amounts(line)  # 행 이름이 「이익률」 · 「승인 하한」
            else:
                for cell in cells[1:]:
                    if _MARKER_RE.search(cell):
                        terms |= _marked_amounts(cell)
        if _SUPPLIER_RE.search(line):
            terms.update(
                name for name in _PAREN_NAME_RE.findall(line) if name.lower() not in _GENERIC_TERMS
            )
    return {term.strip() for term in terms if term.strip()}


def _pattern(term: str) -> re.Pattern:
    """대소문자 무시, 영문·숫자 끝은 경계를 봅니다 — 「$1.5」가 「$1.50」에, 「B2」가 「B2B」에 안 걸리게."""
    head = r"(?<![A-Za-z0-9.,])" if term[:1].isascii() and term[:1].isalnum() else ""
    tail = r"(?![A-Za-z0-9]|[.,]\d)" if term[-1:].isascii() and term[-1:].isalnum() else ""
    return re.compile(head + re.escape(term) + tail, re.IGNORECASE)


def redact(text: str, terms) -> str:
    """금지 문자열을 「[비공개]」로. 긴 것부터 — 「$1.50」을 지운 뒤에 「$1.5」를 찾습니다."""
    for term in sorted({term for term in terms if term}, key=len, reverse=True):
        text = _pattern(term).sub(REDACTED, text)
    return text


def _clean_terms(raw, body: str) -> set[str]:
    """모델이 적은 금지 문자열 중 실제로 본문에 있고, 평범한 낱말이 아닌 것만."""
    flat = " ".join(body.split())
    kept: set[str] = set()
    for item in list(raw or [])[:60]:
        term = " ".join(str(item).split()).strip(" '\"`*")
        if not 2 <= len(term) <= 60 or term.lower() in _GENERIC_TERMS:
            continue
        amounts = [found.group(0) for found in _AMOUNT_RE.finditer(term)]
        if amounts:
            kept.update(amount for amount in amounts if _pattern(amount).search(flat))
        elif re.match(r"[~≈약]?\s*\d", term):
            continue  # 단위가 돈·%가 아닌 숫자(300분 · 18,000) — 고객이 쓴 말과 겹칩니다
        elif _pattern(term).search(flat):
            kept.add(term)
    return kept


# ---------------------------------------------------------------- 꼬리표 (flash 한 번)


class SectionLabel(BaseModel):
    idx: int
    kind: Kind
    applies_to: list[Situation] = []
    topics: list[str] = []
    confidential: bool = False
    terms: list[str] = []


class OrganizeResult(BaseModel):
    sections: list[SectionLabel]


def body_sha(body: str | None) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def _template_goes_to_model(key: str | None) -> bool:
    key = key or ""
    return not key.startswith(SIGNATURE_KEY_PREFIX) and key.removesuffix("_en") not in _VALUE_KEYS


@dataclass(frozen=True)
class _Source:
    kind: str
    id: int
    title: str
    body: str
    # 모델에 안 보내는 글 — 「사람만 본다」 정책 문서, 서명, 값만 든 템플릿 행.
    people_only: bool

    @property
    def sha(self) -> str:
        return body_sha(self.body)

    @classmethod
    def of(cls, row) -> _Source:
        if isinstance(row, PolicySource):
            return cls(
                POLICY, row.id, row.title or row.label or "", row.body or "",
                (row.model_access or "customer_context") != "customer_context",
            )
        return cls(
            TEMPLATE, row.id, row.name or row.key or "", row.body or "",
            not _template_goes_to_model(row.key),
        )


def _render_for_labeling(body: str, spans) -> str:
    return "\n\n".join(
        f"=== section {index} · {path or '(앞부분)'} ===\n{body[start:end].strip()}"
        for index, (start, end, path) in enumerate(spans)
    )


def _ask_model(source: _Source, spans, llm) -> list[SectionLabel]:
    if len(source.body) > _MAX_BODY_CHARS:
        raise OrganizerError(f"본문이 {_MAX_BODY_CHARS:,}자를 넘어 모델에 보내지 않았습니다.")
    from .client import LLMClient

    result = (llm or LLMClient()).complete(
        "policy/organize",
        {
            "title": source.title,
            "source_kind": "이메일 템플릿" if source.kind == TEMPLATE else "정책 문서",
            "sections": _render_for_labeling(source.body, spans),
        },
        schema=OrganizeResult,
        tier="flash",
        max_tokens=LABEL_MAX_TOKENS,  # 한도에 생각 토큰이 들어간다 — `llm/client._THINKING_LEVEL_BY_TIER`
    )
    labels: dict[int, SectionLabel] = {}
    for label in result.sections:
        labels.setdefault(label.idx, label)
    missing = [index for index in range(len(spans)) if index not in labels]
    if missing:
        # 꼬리표 없는 구간을 아무 종류로나 채우면 원가가 「기타」로 실립니다. 통째로 실패로 둡니다.
        raise OrganizerError(f"구간 {len(spans)}개 중 {len(missing)}개에 꼬리표가 없습니다.")
    return [labels[index] for index in range(len(spans))]


def _records(source: _Source, llm) -> list[dict]:
    spans = split_sections(source.body)
    if not spans:
        return []
    labels = None if source.people_only else _ask_model(source, spans, llm)
    records = []
    for index, (start, end, heading) in enumerate(spans):
        terms = deterministic_terms(source.body[start:end])
        if labels is None:
            # 「사람만 본다」 — 모델이 안 봤으니 꼬리표도 없습니다. 전부 기밀입니다.
            kind, applies, topics, confidential = "confidential", [], [], True
        else:
            label = labels[index]
            kind = label.kind
            applies = list(dict.fromkeys(label.applies_to))
            if not applies or ANY in applies:
                applies = [ANY]
            topics = [" ".join(topic.split())[:40] for topic in label.topics[:8] if topic.strip()]
            confidential = label.confidential or kind == "confidential"
            terms |= _clean_terms(label.terms, source.body)
        records.append({
            "idx": index,
            "start_offset": start,
            "end_offset": end,
            "heading": heading[:300] or None,
            "kind": kind,
            "applies_to": applies,
            "topics": topics,
            "confidential": confidential,
            "terms": sorted(terms),
        })
    return records


def _write(session, source: _Source, records: list[dict]) -> None:
    forget(session, source.kind, source.id)
    session.add_all(
        PolicySection(
            source_kind=source.kind,
            source_id=source.id,
            body_sha256=source.sha,
            organizer_version=ORGANIZER_VERSION,
            **record,
        )
        for record in records
    )
    session.flush()


def forget(session, kind: str, source_id: int) -> None:
    """그 문서의 지도를 지웁니다(커밋은 부르는 쪽). 문서를 지우거나 「사람만 본다」가 바뀔 때."""
    session.query(PolicySection).filter(
        PolicySection.source_kind == kind, PolicySection.source_id == source_id
    ).delete(synchronize_session=False)


def organize_source(session, row, *, llm=None) -> int:
    """이 행(정책 문서 또는 이메일 템플릿) 하나를 지금 정리해 ``session`` 에 씁니다(커밋은 부르는 쪽).

    지도가 지금 것이어도 다시 합니다. 모델은 이 세션이 열린 채로 부르므로, 폴러와 저장 직후의
    정리는 이 함수 대신 세션을 닫고 모델을 부르는 ``organize_pending`` · ``backfill`` 을 씁니다.
    """
    source = _Source.of(row)
    records = _records(source, llm)
    _write(session, source, records)
    return len(records)


# ---------------------------------------------------------------- 대기열 (폴러 · 저장 직후)

# 폴러와 저장 직후의 정리가 같은 문서를 동시에 쓰지 않게. 프로세스 하나에서 도는 앱입니다.
# ponytail: 프로세스 안의 자물쇠 — 워커가 여럿이 되면 유니크 인덱스가 늦게 온 쪽을 실패시킵니다.
_LOCK = threading.Lock()


def _sessions():
    from ..db.session import SessionLocal

    return SessionLocal


@contextmanager
def _opened(session):
    if session is not None:
        yield session
        return
    with _sessions()() as own:
        yield own


def _maps(session, kind: str, ids=None) -> dict[int, list[PolicySection]]:
    query = session.query(PolicySection).filter(PolicySection.source_kind == kind)
    if ids is not None:
        ids = list(ids)
        if not ids:
            return {}
        query = query.filter(PolicySection.source_id.in_(ids))
    maps: dict[int, list[PolicySection]] = {}
    for row in query.order_by(PolicySection.source_id, PolicySection.idx):
        maps.setdefault(row.source_id, []).append(row)
    return maps


def _is_current(rows, body: str | None) -> bool:
    """이 지도가 지금 본문의 것이고, 오프셋이 본문을 빈틈없이 덮는가."""
    if not rows:
        return False
    sha = body_sha(body)
    if any(row.body_sha256 != sha or row.organizer_version != ORGANIZER_VERSION for row in rows):
        return False
    return _covers(
        [(row.start_offset, row.end_offset) for row in sorted(rows, key=lambda row: row.idx)],
        len(body or ""),
    )


def _failures(session) -> list[dict]:
    return [
        event.payload or {}
        for event in session.query(Event).filter(Event.kind == FAILED_EVENT).order_by(Event.id)
    ]


def _failure_key(payload: dict):
    return payload.get("source_kind"), payload.get("source_id"), payload.get("body_sha256")


def _prune_orphans(session) -> None:
    """지워진 문서의 지도. 정책 문서는 지우는 라우트가 같이 지우고, 템플릿은 여기서 치웁니다."""
    alive = {
        POLICY: {row_id for (row_id,) in session.query(PolicySource.id)},
        TEMPLATE: {row_id for (row_id,) in session.query(EmailTemplate.id)},
    }
    for kind, source_id in set(session.query(PolicySection.source_kind, PolicySection.source_id)):
        if source_id not in alive.get(kind, set()):
            forget(session, kind, source_id)


def _candidates(session, *, capped: bool, source_id: int | None = None) -> list[_Source]:
    rows: list = list(session.query(PolicySource).order_by(PolicySource.id))
    if source_id is not None:
        rows = [row for row in rows if row.id == source_id]
    else:
        rows += [
            row for row in session.query(EmailTemplate).order_by(EmailTemplate.id)
            if _template_goes_to_model(row.key)
        ]
    sources = [source for source in map(_Source.of, rows) if source.body.strip()]
    maps = {POLICY: _maps(session, POLICY), TEMPLATE: _maps(session, TEMPLATE)}
    tries = Counter(
        _failure_key(payload) for payload in _failures(session)
        if payload.get("organizer_version") == ORGANIZER_VERSION
    ) if capped else Counter()
    return [
        source for source in sources
        if not _is_current(maps[source.kind].get(source.id), source.body)
        and tries[(source.kind, source.id, source.sha)] < _MAX_AUTO_ATTEMPTS
    ]


def _run_one(source: _Source, llm) -> int | None:
    """한 문서. 모델은 세션 밖에서 부르고, 쓰기 직전에 그 사이 본문이 안 바뀌었는지 봅니다."""
    factory = _sessions()
    try:
        records = _records(source, llm)
    except Exception as exc:
        logger.warning("문서 정리 실패: %s #%s", source.kind, source.id, exc_info=True)
        with factory() as session:
            session.add(Event(kind=FAILED_EVENT, payload={
                "source_kind": source.kind,
                "source_id": source.id,
                "body_sha256": source.sha,
                "organizer_version": ORGANIZER_VERSION,
                # 본문은 안 적습니다 — 예외 이름과 그 메시지 앞머리만.
                "error": f"{type(exc).__name__}: {exc}"[:300],
            }))
            session.commit()
        return None
    with factory() as session:
        row = session.get(PolicySource if source.kind == POLICY else EmailTemplate, source.id)
        if row is None:
            return None
        now = _Source.of(row)
        if now.body != source.body or now.people_only != source.people_only:
            return None  # 정리하는 사이 고쳤습니다 — 다음 회차가 새 본문으로 합니다
        _write(session, source, records)
        session.commit()
    logger.info("문서 정리: %s #%s 구간 %d개", source.kind, source.id, len(records))
    return len(records)


def organize_pending(limit: int = 1) -> int:
    """폴러 한 회차 — 지도가 없거나 낡은 문서를 ``limit`` 편까지. 정리한 편 수를 돌려줍니다.

    대기열은 「지금 본문의 지도가 없다」 그 자체입니다. 다 정리돼 있으면 조회 두어 번으로
    끝나고 모델은 안 부릅니다. 같은 본문으로 ``_MAX_AUTO_ATTEMPTS`` 번 실패한 문서는 건너뜁니다.
    """
    if not _LOCK.acquire(blocking=False):
        return 0  # 저장 직후의 정리가 도는 중
    try:
        with _sessions()() as session:
            _prune_orphans(session)
            session.commit()
            todo = _candidates(session, capped=True)[: max(1, limit)]
        return sum(1 for source in todo if _run_one(source, None) is not None)
    finally:
        _LOCK.release()


def backfill(limit=None, llm=None, *, source_id: int | None = None) -> dict:
    """지도가 지금 것이 아닌 문서를 전부(``limit`` 편까지) 정리합니다 — 실패 횟수와 무관하게.

    ``source_id`` 는 정책 문서 한 편만. 서명과 값만 든 템플릿 행(링크·이름)은 안 합니다.
    """
    result = {"organized": 0, "failed": 0, "sections": 0}
    with _LOCK:
        with _sessions()() as session:
            _prune_orphans(session)
            session.commit()
            todo = _candidates(session, capped=False, source_id=source_id)
        for source in todo if limit is None else todo[:limit]:
            written = _run_one(source, llm)
            if written is None:
                result["failed"] += 1
            else:
                result["organized"] += 1
                result["sections"] += written
    return result


# ---------------------------------------------------------------- 읽는 쪽


def _vocabulary(session) -> tuple[set[str], set[str], set[str]]:
    """(금지 문자열, 내부 금액, 코드가 뽑은 금지 문자열). 모두 모든 문서에서 — 지금 초안에 실리는
    문서만이 아닙니다. 「사람만 본다」 문서도 코드 추출은 지납니다(모델은 안 지납니다).

    금지 문자열은 정리된 지도의 것 + 코드가 뽑은 것입니다. 지도는 낡았어도 새 지도로 바뀔 때까지 그
    문자열을 계속 셉니다 — 많이 지우는 쪽으로 틀립니다. 모델이 적은 문자열이 공개 가격·완성 예시·
    템플릿에 그대로 있으면 착오로 보고 뺍니다(우리가 고객에게 실제로 하는 말입니다). 코드가 뽑은 것은
    그래도 남습니다.

    내부 금액은 문서가 내부 단가·기밀 구간에 적은 금액입니다. 그 금액이 규칙·사실 구간에 되풀이돼
    있으면(「단가 바닥: …」) 거기서 지웁니다 — 그 구간이 고객 답장 아닌 회신에도 실리기 때문입니다.
    """
    bodies = {(POLICY, row_id): body or "" for row_id, body in session.query(PolicySource.id, PolicySource.body)}
    bodies.update(
        ((TEMPLATE, row_id), body or "")
        for row_id, key, body in session.query(EmailTemplate.id, EmailTemplate.key, EmailTemplate.body)
        if not (key or "").startswith(SIGNATURE_KEY_PREFIX)
    )
    stored: set[str] = set()
    public: list[str] = []
    internal: set[str] = set()
    for section in session.query(PolicySection):
        body = bodies.get((section.source_kind, section.source_id))
        if body is None:
            continue  # 지워진 문서의 지도
        stored.update(section.terms or ())
        if section.body_sha256 != body_sha(body):
            continue  # 낡은 지도의 오프셋은 지금 본문을 가리키지 않습니다
        text = body[section.start_offset:section.end_offset]
        if section.confidential or section.kind in ("confidential", "internal_price"):
            internal |= {found.group(0) for found in _MONEY_RE.finditer(text)}
        elif section.kind in _PUBLIC_KINDS:
            public.append(text)
    said = " ".join(" ".join(public).split())
    terms = {term for term in stored if term and not _pattern(term).search(said)}
    marked: set[str] = set()
    for body in bodies.values():
        marked |= deterministic_terms(body)
    return terms | marked, internal, marked


def _fits(section: PolicySection) -> bool:
    """싣는가 — 기밀만 뺍니다. **상황으로는 거르지 않습니다** (2026-10-06 평가).

    모델이 단 「쓰이는 상황」 꼬리표로 걸렀더니 판정 문서의 규칙(「연 약정은 물량으로 정하지 않는다」·「분할을
    먼저 제안하지 않는다」)이 「첫 회신」으로 달려 후속 회신에서 통째로 빠졌고, 후속 회신의 치명적 실수가 전부
    거기서 나왔습니다. 꼬리표는 돌릴 때마다 조금씩 달라서, 거르면 어느 규칙이 초안에 닿을지가 그때그때 바뀝니다.
    어느 회신에 어느 **문서**를 붙일지는 콘솔의 배치(`policy_docs.PLACEMENTS`)가 정합니다 — 내부 단가 문서가
    「그 이후 회신에」인 것처럼. 꼬리표는 구간 머리에 「주로 …」로 알려 주기만 합니다.
    """
    return not section.confidential and section.kind not in _NEVER


def _full_text(documents, terms) -> str:
    """정리 전 문서 — 오늘과 같은 모양(`policy_context.render_rules`), 금지 문자열만 지웁니다."""
    return "\n\n".join(
        f"# {doc.label}\n[policy source_id={doc.id}; version={doc.version}]\n"
        f"{redact((doc.body or '').strip(), terms)}"
        for doc in documents
    )


def knowledge_for(documents, situation: str, *, session=None) -> dict:
    """초안에 실을 회사 지식 — ``PolicySnapshot`` 의 문서(이미 권한·단계로 거른 것)에서.

    지도가 지금 것인 문서는 기밀 구간만 빼고 **전부** 종류별로 묶어 원문 그대로 싣습니다. 이번 상황
    (``first_reply`` · ``answer_reply`` · ``nudge`` · ``closing_notice``)은 머리말로 알리고, 구간마다
    「주로 쓰이는 회신」을 붙입니다 — 거르지 않습니다(`_fits`). 금지 문자열은 어느 구간에서든
    「[비공개]」가 됩니다. 지도가 없거나 낡은 문서는 예전처럼 본문 통째로 갑니다(금지 문자열만 지웁니다).

    돌려주는 것: ``text`` · ``mode``(``sections`` 전부 정리됨 · ``full`` 하나도 안 됨 ·
    ``mixed``) · ``section_ids``(「문서 id:구간 번호」) · ``map_sha`` · ``fallback_ids``.

    조회가 실패하면 빈 문맥이 아니라 ``PolicyContextError`` 입니다.
    """
    if situation not in SITUATIONS:
        raise ValueError(f"모르는 회신 상황입니다: {situation!r}")
    docs = [doc for doc in documents if (doc.body or "").strip()]
    try:
        with _opened(session) as active:
            maps = _maps(active, POLICY, [doc.id for doc in docs])
            terms, internal, marked = _vocabulary(active)
    except Exception as exc:
        raise PolicyContextError("정리된 정책을 읽지 못했습니다. 초안을 다시 생성해 주세요.") from exc
    # **모델이 적은 숫자는 그 구간에서만 지웁니다** (2026-10-06 실측). 단가 문서의 「플랜 전체 지도」
    # 구간이 우리 Enterprise 단가를 금지 문자열로 적었고, 그것이 문서 전체에 걸리자 견적 계산용
    # 단가표(4-1)의 같은 숫자까지 「[비공개]」가 되어 후속 회신이 견적을 쓸 길이 없어졌습니다.
    # 이름(경쟁사·공급사)과 코드가 표시로 뽑은 것(비공개·❌·원가·이익률 칸)은 어디서나 지웁니다.
    everywhere = marked | {term for term in terms if not any(ch.isdigit() for ch in term)}

    groups: dict[str, list[str]] = {kind: [] for kind, _title in _GROUPS}
    section_ids: list[str] = []
    used: list = []
    fallback: list = []
    for doc in docs:
        rows = maps.get(doc.id, [])
        if not _is_current(rows, doc.body):
            fallback.append(doc)
            continue
        used.append([
            doc.id, rows[0].body_sha256, ORGANIZER_VERSION,
            [[row.idx, row.kind, list(row.applies_to or []), bool(row.confidential)] for row in rows],
        ])
        label = _clean_heading(doc.label or doc.title or "")
        for row in rows:
            if not _fits(row):
                continue
            text = doc.body[row.start_offset:row.end_offset].strip()
            if not text:
                continue
            if row.kind not in _MONEY_KINDS:
                # 규칙·사실 구간에 되풀이된 내부 금액. 모델이 단가표를 「규칙」으로 잘못 달아도
                # 그 숫자는 내부 단가 구간에 있는 한 여기서 지워집니다.
                text = redact(text, internal)
            heading = row.heading or ""
            name = heading if heading.startswith(label) else " › ".join(p for p in (label, heading) if p)
            own = {term for term in (row.terms or ()) if term in terms}
            only = [_SITUATION_NAMES[value] for value in (row.applies_to or ()) if value in _SITUATION_NAMES]
            when = f" ({' · '.join(only)}에만 적용)" if only and ANY not in (row.applies_to or ()) else ""
            groups[row.kind].append(
                f"[policy source_id={doc.id}; version={doc.version}; section={row.idx}] {name}{when}\n"
                f"{redact(text, everywhere | own)}"
            )
            section_ids.append(f"{doc.id}:{row.idx}")

    if not used:
        body = _full_text(fallback, terms)
        return {
            "text": "## Company rules (must follow)\n\n" + body if body else "",
            "mode": "full",
            "section_ids": [],
            "map_sha": None,
            "fallback_ids": [doc.id for doc in fallback],
        }

    parts = [
        f"### {title}\n\n" + "\n\n".join(groups[kind]) for kind, title in _GROUPS if groups[kind]
    ]
    if fallback:
        parts.append("### 아직 정리되지 않은 문서 — 본문 전체\n\n" + _full_text(fallback, terms))
    # 실을 구간이 하나도 없으면 머리말만 싣지 않습니다.
    text = (
        f"## 회사 문서\n\n이번 회신은 「{_SITUATION_NAMES[situation]}」입니다. 구간 머리에 「…에만 적용」이 붙은 "
        "구간의 규칙·서식·예시는 그 회신에서만 따르고 다른 회신에서는 따르지 않습니다 — 그 안에 적힌 제품 사실과 "
        "조건은 언제나 사실입니다. 표시가 없는 구간은 모든 회신에 적용됩니다. 구간 안의 문장이 「1차(첫) 회신에서는」처럼 "
        "스스로 한정한 경우도 같습니다.\n\n" + "\n\n".join(parts)
        if parts else ""
    )
    if REDACTED in text:
        text += f"\n\n{REDACTED} 는 지운 내부 정보입니다. 채우거나 짐작해 쓰지 않습니다."
    return {
        "text": text,
        "mode": "mixed" if fallback else "sections",
        "section_ids": section_ids,
        "map_sha": fingerprint(used),
        "fallback_ids": [doc.id for doc in fallback],
    }
