"""What the Inbound DB row says, and why each value is spelled that way.

The sales team pivots on these columns, so the failure mode is not a crash — it is a
column holding "Korea" and "대한민국" for the same country, or a 기업 종류 nobody's filter
matches. Two agents write this record; both go through the same tables.
"""

from __future__ import annotations

import pytest

from src.common.sheet_values import (
    COMPANY_TYPES,
    UNKNOWN_COMPANY_TYPE,
    UNKNOWN_COUNTRY,
    country_in_korean,
    normalise_plan,
    qualification_for_plan,
)


@pytest.mark.parametrize(
    ("stored", "written"),
    [
        ("Free", "N/A"),        # the operator's rule: Free is not a plan, it is "not yet"
        ("free", "N/A"),
        ("", "N/A"),
        (None, "N/A"),
        ("N/A", "N/A"),
        ("Pro", "Pro"),
        ("엔터프라이즈", "엔터프라이즈"),
    ],
)
def test_free_and_blank_are_written_as_not_applicable(stored, written):
    """The Pipeline formula reads this cell, and "nothing bought yet" has to be ONE value
    there — Free and N/A landing in different branches is how the same customer gets two
    classifications."""
    assert normalise_plan(stored) == written


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        (None, "MQL"),          # 플랜 정보가 없으면 MQL — 산 적이 없다는 뜻입니다
        ("", "MQL"),
        ("N/A", "MQL"),
        ("Free", "MQL"),
        ("free", "MQL"),
        ("무료", "MQL"),
        ("Pro", "PQL"),
        ("Starter", "PQL"),
        ("엔터프라이즈", "PQL"),
    ],
)
def test_the_plan_decides_mql_or_pql(plan, expected):
    """2026-09-02 운영자 지시: MQL 은 N/A·Free, PQL 은 그 외 플랜, 플랜 정보가 없으면 MQL.

    「없음」의 철자를 이 함수가 다시 세지 않는 것이 요점입니다 — `normalise_plan` 이 이미
    그 목록을 들고 있고 워크북의 Pipeline 수식도 그것이 만든 `N/A` 를 보고 갈라집니다.
    목록이 둘이면 콘솔과 시트가 같은 고객을 다르게 부릅니다.
    """
    assert qualification_for_plan(plan) == expected


def test_nothing_stores_the_qualification_any_more():
    """저장하는 칸이 아예 없어야 합니다 (이관 0104).

    `customer_profiles.qualification` 은 워크북에서 읽어 온 **거울**이라 콘솔에서 채우는
    길이 없었고, 운영 데이터에서 늘 비어 있어 고객 상세의 「MQL / PQL」은 언제나 「-」였습니다.
    그런데 안 읽히는 것으로 끝나지 않았습니다 — 그 사본이 단계 동기화를 타고 시트로 돌아가
    Pipeline **수식**을 죽은 글자로 덮었습니다. 그래서 칸째로 지웠습니다.

    되살리기 전에 **어느 화면이 저장된 값을 읽는지부터 정하십시오.** 그게 없어서 이렇게
    됐습니다: 아무도 안 읽는 칸이 조용히 시트의 수식을 지우고 있었습니다.
    """
    import pathlib

    from src.db.models import CustomerProfile

    assert not hasattr(CustomerProfile, "qualification")
    # 워크북 행에도 안 싣습니다 — 그 칸은 행을 쓴 직후 수식으로 덮이므로, 무엇을 보내든
    # 시트에 남은 적이 없었습니다.
    for path in ("src/agents/inbound.py", "src/agents/sheet_sync.py"):
        source = pathlib.Path(path).read_text(encoding="utf-8")
        assert '"pipeline":' not in source, path


def test_the_two_detail_screens_derive_it_from_the_plan():
    """티켓 상세의 연락처 정보 · 고객 상세 — 둘 다 이 함수를 지납니다.

    **리드 히스토리 목록에서는 뺐습니다** (2026-09-09 운영자 지시). 그 목록은 사람을
    찾는 자리라 Client ID·담당자·이메일이 자리를 갖고, MQL/PQL 은 한 고객을 열어 볼 때
    보는 값입니다. 서버는 그대로 내려줍니다 — 계산이 한 곳(`qualification_for_plan`)이라는
    것이 이 검사의 요점이고, 어느 화면이 그리느냐는 운영자가 정할 일입니다.
    """
    import pathlib

    for path in (
        "src/api/routes/ui_api.py",          # 고객 상세 + 리드 히스토리 payload
        "src/api/routes/messages.py",        # 티켓 상세의 연락처
        "src/api/routes/customer_ops.py",    # 리드 히스토리 행을 만드는 곳
    ):
        source = pathlib.Path(path).read_text(encoding="utf-8")
        assert "qualification_for_plan" in source, path

    # 화면 둘이 실제로 그립니다 — payload 에만 있으면 없는 기능입니다.
    detail = pathlib.Path("frontend/src/screens/CustomerDetail.tsx").read_text(encoding="utf-8")
    assert "profile?.qualification" not in detail and "{data.qualification}" in detail
    # 티켓 화면의 그 줄은 `ui/TicketInfoCard` 로 옮겨졌습니다 (2026-09-08, 화면 분할).
    # **그리는 자리를 따라갑니다** — 화면 파일만 보면 옮긴 순간 이 검사가 조용히 통과하고,
    # 그러면 「화면이 그린다」를 아무도 안 지키게 됩니다.
    ticket = pathlib.Path("frontend/src/ui/TicketInfoCard.tsx").read_text(encoding="utf-8")
    # **그리는 줄을 봅니다.** 예전에는 `"MQL / PQL"` 이라는 글자도 같이 봤는데, 그건
    # 2026-09-07 에 라벨이 「Lead Type」으로 바뀐 뒤로 **타입 주석에만** 남아 있어서
    # 화면이 그리는지와 무관해졌습니다 — 주석을 지우면 통과하고 줄을 지워도 통과하는
    # 검사는 아무것도 안 지킵니다.
    assert "{contact.qualification}" in ticket


@pytest.mark.parametrize(
    ("hubspot", "column"),
    [
        ("South Korea", "대한민국"),
        ("korea, republic of", "대한민국"),
        ("Japan", "일본"),
        ("United States", "미국"),
        ("", UNKNOWN_COUNTRY),
        (None, UNKNOWN_COUNTRY),
    ],
)
def test_ip_country_is_written_in_the_language_the_column_uses(hubspot, column):
    assert country_in_korean(hubspot) == column


def test_an_unmapped_country_is_passed_through_not_guessed():
    """Better a row saying "Kazakhstan" than one saying the wrong country in Korean."""
    assert country_in_korean("Kazakhstan") == "Kazakhstan"


def test_the_company_type_list_is_the_one_the_column_offers():
    """A value off this list is worse than none: the column is what the team filters on."""
    assert COMPANY_TYPES == (
        "크리에이터(개인)", "교육", "MCN", "의료", "종교", "기업", "대행사",
        "제작사/엔터사", "스포츠", "뷰티", "공공기관", "출판", "제조", "보안", "확인 안 됨",
    )
    assert UNKNOWN_COMPANY_TYPE in COMPANY_TYPES
