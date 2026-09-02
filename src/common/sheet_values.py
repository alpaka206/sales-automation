"""The vocabulary the sales workbook expects, in one place.

Two agents build the Inbound DB record — the live inbound run and the backfill sweep —
and each used to spell these values itself. A column filled with "Korea" by one and
"대한민국" by the other is not a column anyone can filter on, which is what these tables
are for.
"""

from __future__ import annotations

# 기업 종류. The sales team filters on this column, so it is a closed list: anything not
# on it is 확인 안 됨 rather than a new spelling nobody pivots on.
COMPANY_TYPES: tuple[str, ...] = (
    "크리에이터(개인)",
    "교육",
    "MCN",
    "의료",
    "종교",
    "기업",
    "대행사",
    "제작사/엔터사",
    "스포츠",
    "뷰티",
    "공공기관",
    "출판",
    "제조",
    "보안",
    "확인 안 됨",
)
UNKNOWN_COMPANY_TYPE = "확인 안 됨"

# 구독 플랜. Free is written as N/A on the operator's instruction: the Pipeline formula
# reads this cell, and "nothing bought yet" is one state there, not two.
#
# **공개 이름인 이유**: 워크북의 Pipeline 수식도 이 목록으로 만듭니다
# (`google_sheets._pipeline_formula`). 「아직 아무것도 안 샀다」의 철자를 콘솔과 시트가 따로
# 세면, 같은 고객을 한쪽은 MQL 다른 쪽은 PQL 이라고 부릅니다 — 그리고 그건 두 화면을 나란히
# 놓고 보기 전에는 안 보입니다.
PLAN_AS_NOT_APPLICABLE = frozenset({"free", "n/a", "na", "none", "없음", "무료"})


def normalise_plan(value: str | None) -> str:
    """The plan as the workbook spells it. Free and blank both become N/A."""
    text = (value or "").strip()
    return "N/A" if text.lower() in PLAN_AS_NOT_APPLICABLE or not text else text


def qualification_for_plan(value: str | None) -> str:
    """MQL / PQL — **플랜이 정합니다** (2026-09-02 운영자 지시).

    아직 아무것도 안 산 상태(플랜 없음 · Free · N/A)가 MQL 이고, 그 외 플랜은 전부
    PQL 입니다. 플랜 정보가 아예 없는 연락처도 MQL 입니다 — 산 적이 없다는 뜻이니까요.

    **저장하지 않고 플랜에서 파생합니다.** 둘 다 저장하면 플랜을 고친 뒤 이 값을 안 고친
    행이 반드시 생기고, 그건 화면에 안 보입니다(고객 종류를 번호대에서 되짚는 것과 같은
    이유, 0065). `customer_profiles.qualification` 열은 **지웠습니다**(이관 0104) — 아무도
    안 읽는 채로 남아, 단계 동기화를 타고 시트로 돌아가 Pipeline 수식을 덮고 있었습니다.

    「없음」의 철자를 여기서 다시 세지 않습니다 — `normalise_plan` 이 이미 그 목록을 들고
    있고(free · n/a · na · none · 없음 · 무료 · 빈칸), 워크북의 Pipeline 수식도 그것이
    만든 `N/A` 를 보고 갈라집니다. 목록이 둘이면 콘솔과 시트가 다른 답을 냅니다.
    """
    return "MQL" if normalise_plan(value) == "N/A" else "PQL"


# IP country. HubSpot reports the IP-derived country in English ("South Korea", "Japan");
# the column is Korean, and the two spellings in one column cannot be counted together.
# Only the countries this inbound funnel actually sees are listed — an unmapped one is
# passed through as HubSpot wrote it rather than guessed at.
_COUNTRY_KO: dict[str, str] = {
    "south korea": "대한민국",
    "korea": "대한민국",
    "korea, republic of": "대한민국",
    "republic of korea": "대한민국",
    "united states": "미국",
    "united states of america": "미국",
    "japan": "일본",
    "china": "중국",
    "taiwan": "대만",
    "hong kong": "홍콩",
    "singapore": "싱가포르",
    "viet nam": "베트남",
    "vietnam": "베트남",
    "thailand": "태국",
    "indonesia": "인도네시아",
    "malaysia": "말레이시아",
    "philippines": "필리핀",
    "india": "인도",
    "united kingdom": "영국",
    "germany": "독일",
    "france": "프랑스",
    "italy": "이탈리아",
    "spain": "스페인",
    "netherlands": "네덜란드",
    "canada": "캐나다",
    "australia": "호주",
    "new zealand": "뉴질랜드",
    "brazil": "브라질",
    "mexico": "멕시코",
    "united arab emirates": "아랍에미리트",
    "saudi arabia": "사우디아라비아",
    "turkey": "튀르키예",
    "russia": "러시아",
    "poland": "폴란드",
    "sweden": "스웨덴",
}
UNKNOWN_COUNTRY = "알 수 없음"


def country_in_korean(value: str | None) -> str:
    """HubSpot's IP country, in the language the column is written in."""
    text = (value or "").strip()
    if not text:
        return UNKNOWN_COUNTRY
    return _COUNTRY_KO.get(text.lower(), text)
