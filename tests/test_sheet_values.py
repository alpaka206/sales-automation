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


def test_the_three_screens_derive_it_from_the_plan():
    """리드 히스토리 · 티켓 상세의 연락처 정보 · 고객 상세 — 셋 다 이 함수를 지납니다.

    저장한 열(`customer_profiles.qualification`)을 읽으면 안 됩니다. 그것은 워크북에서
    읽어 온 **거울**이고(`sheet_sync`) 콘솔에서 채우는 길이 없어 운영 데이터에서 늘 비어
    있었습니다 — 그래서 고객 상세의 「MQL / PQL」은 언제나 「-」였습니다(2026-09-02 운영자
    지적). 저장하기 시작하면 플랜을 고친 뒤 이 값을 안 고친 행이 반드시 생기고, 그건
    화면에 안 보입니다.

    워크북으로 나가는 길(`update_inbound_stage(pipeline=...)`)은 그대로 그 열을 봅니다.
    거기서는 시트의 Pipeline **수식**이 같은 판단을 하고 있고, 우리가 값을 써 넣으면 그
    수식이 지워집니다 — 콘솔이 그리는 값과 시트가 계산하는 값은 서로 다른 길입니다.
    """
    import pathlib

    for path in (
        "src/api/routes/ui_api.py",          # 고객 상세 + 리드 히스토리 payload
        "src/api/routes/messages.py",        # 티켓 상세의 연락처
        "src/api/routes/customer_ops.py",    # 리드 히스토리 행을 만드는 곳
    ):
        source = pathlib.Path(path).read_text(encoding="utf-8")
        assert "qualification_for_plan" in source, path

    # 화면 셋이 실제로 그립니다 — payload 에만 있으면 없는 기능입니다.
    detail = pathlib.Path("frontend/src/screens/CustomerDetail.tsx").read_text(encoding="utf-8")
    assert "profile?.qualification" not in detail and "{data.qualification}" in detail
    leads = pathlib.Path("frontend/src/screens/Customers.tsx").read_text(encoding="utf-8")
    assert "row.qualification" in leads and "MQL / PQL" in leads
    ticket = pathlib.Path("frontend/src/screens/MessageDetail.tsx").read_text(encoding="utf-8")
    assert "contact.qualification" in ticket and "MQL / PQL" in ticket


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
