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
