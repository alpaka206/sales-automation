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
_PLAN_AS_NOT_APPLICABLE = {"free", "n/a", "na", "none", "없음", "무료"}


def normalise_plan(value: str | None) -> str:
    """The plan as the workbook spells it. Free and blank both become N/A."""
    text = (value or "").strip()
    return "N/A" if text.lower() in _PLAN_AS_NOT_APPLICABLE or not text else text


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
