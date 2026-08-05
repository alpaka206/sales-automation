"""문의 유형 — 하나의 정의.

분류기가 뱉는 값, 목록에 보이는 한글 이름, 그리고 그 유형에 어떤 문서를 붙일지가 전부 이
튜플에서 나옵니다. 세 곳에 흩어져 있으면 분류기는 ``credits`` 를 내는데 화면은 그 유형을
모르고, 문서는 아무 유형에도 안 걸리는 상태가 조용히 생깁니다.

``qualified=False`` 는 **세일즈 리드가 아니라는 뜻**입니다(B2B 리드 대응 정책 §1). 회신을
안 한다는 뜻이 아니라 — CS 문의는 CS 가이드대로, 영업·홍보 문의는 소개 문서로 나갑니다 —
파이프라인에 올릴 대상이 아니라는 표시이고, 목록에서 UnQualified 로 보입니다.

어떤 문서를 볼지는 **여기 없습니다.** 그건 모델이 문서 목록을 보고 고릅니다(llm/knowledge.py).
유형은 그 판단에 넘어가는 힌트일 뿐입니다 — 정책도 문서 이름도 바뀌므로, "이 유형이면 저
문서" 를 코드에 굳히면 바뀔 때마다 아무 흔적 없이 끊깁니다.
"""

from __future__ import annotations

# (key, 화면에 보이는 이름, 세일즈 리드인가)
INQUIRY_CATEGORIES: tuple[tuple[str, str, bool], ...] = (
    ("support", "CS 문의", False),
    ("spam", "영업·홍보", False),
    ("pricing_question", "견적·가격", True),
    ("purchase_inquiry", "전반적 소개", True),
    ("business_plan", "비즈니스 플랜", True),
    ("languages", "지원 언어", True),
    ("plan_features", "B2B 플랜 기능", True),
    ("credits", "크레딧 차감", True),
    ("partnership", "제휴·파트너십", True),
    ("recruiting", "채용", False),
    ("other", "기타", True),
)

CATEGORY_LABELS: dict[str, str] = {key: label for key, label, _ok in INQUIRY_CATEGORIES}
UNQUALIFIED: frozenset[str] = frozenset(
    key for key, _label, qualified in INQUIRY_CATEGORIES if not qualified
)


def category_label(key: str | None) -> str:
    """화면에 쓸 이름. 모르는 값이면 그 값을 그대로 — 분류기가 새 유형을 내기 시작하면
    빈칸이 아니라 그 이름이 보여야 눈치챌 수 있습니다."""
    if not key:
        return "—"
    return CATEGORY_LABELS.get(key, key)


def is_unqualified(key: str | None) -> bool:
    return bool(key) and key in UNQUALIFIED
