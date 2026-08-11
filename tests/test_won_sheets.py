"""수주 고객 → 워크북 탭. 어느 행에 쓸지 정하는 규칙만 고정합니다.

값을 만드는 쪽(``_client_row`` 등)은 틀려도 눈에 보입니다 — 시트에 이상한 값이 뜹니다.
**행을 고르는 쪽이 틀리면 남의 행을 덮어씁니다**, 그리고 그건 아무도 못 봅니다. 그래서
``plan_tab`` 만 순수 함수로 떼어 두고 여기서 네 가지를 고정합니다:

1. 빈 시트면 위에서부터 채운다.
2. 자연키(Client ID·차수·회차)가 같으면 **제자리**에 덮어쓴다 (행이 늘지 않는다).
3. 콘솔이 모르는 Client ID 의 행은 **건드리지 않는다** (손으로 쓴 행).
4. 콘솔이 아는 고객인데 콘솔이 안 들고 온 행은 **콘솔이 쓰던 칸만** 비운다 (수식 칸은 그대로).
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from src.agents import won_sheets
from src.agents.won_sheets import CLIENTS, _client_row, plan_tab


def _client(client_id: int, company: str = "서울대학교"):
    return SimpleNamespace(
        client_id=client_id,
        company=company,
        industry="교육",
        country="한국",
        department="GTM",
        contact_name="유명준",
        contact_info="ghldtjd119@snu.ac.kr",
        first_won_on="2026-05-19",
        # 플랜 상태는 저장된 값이 아니라 계약 기간에서 나옵니다(won.plan_status). 기간 안인
        # 계약 하나를 주면 시트 J열이 「사용중」이 됩니다.
        contracts=[
            SimpleNamespace(
                starts_on=(date.today() - timedelta(days=30)).isoformat(),
                ends_on=(date.today() + timedelta(days=300)).isoformat(),
            )
        ],
        owner="이혜람",
    )


def _sheet(*rows: dict) -> dict[str, list[str]]:
    """행 목록을 plan_tab 이 받는 모양(열 문자 → 값 목록)으로."""
    letters = {letter for row in rows for letter in row}
    return {letter: [row.get(letter, "") for row in rows] for letter in letters}


def test_an_empty_sheet_fills_from_the_top():
    plan = plan_tab(CLIENTS, {}, [_client_row(_client(1108))], {"1108"})

    assert [entry["range"] for entry in plan.entered] == [
        "'고객 기본 정보'!A2:A2",
        "'고객 기본 정보'!I2:I2",
    ]
    # 붙어 있는 열은 한 범위로 묶입니다 — 칸마다 한 범위면 요청이 열 배가 됩니다.
    # 사이가 끊기는 것은 B·G(수식)와 D(Website URL)·H(최초 연락일, 시트 것) 때문입니다.
    assert [entry["range"] for entry in plan.raw] == [
        "'고객 기본 정보'!C2:C2",
        "'고객 기본 정보'!E2:F2",
        "'고객 기본 정보'!J2:J2",
    ]
    assert plan.clears == []
    assert plan.dropped == 0


def test_the_same_client_id_is_overwritten_in_place():
    grid = _sheet({"A": "1108", "C": "서울대학교"})
    plan = plan_tab(CLIENTS, grid, [_client_row(_client(1108, "서울대 빅데이터"))], {"1108"})

    assert "'고객 기본 정보'!C2:C2" in [entry["range"] for entry in plan.raw]
    # 새 행이 생기면 안 됩니다.
    assert not any("3" in entry["range"] for entry in plan.entered + plan.raw)
    assert plan.clears == []


def test_a_row_for_an_unknown_client_is_left_alone():
    """콘솔이 모르는 Client ID = 손으로 쓴 행. 콘솔은 그 아래 빈 행에 씁니다."""
    grid = _sheet({"A": "9999", "C": "손으로 적은 고객"})
    plan = plan_tab(CLIENTS, grid, [_client_row(_client(1108))], {"1108"})

    assert plan.clears == []
    assert [entry["range"] for entry in plan.entered] == [
        "'고객 기본 정보'!A3:A3",
        "'고객 기본 정보'!I3:I3",
    ]


def test_a_hand_typed_row_is_adopted_once_the_console_knows_that_client():
    """운영자가 먼저 채워 둔 1108 행. 콘솔이 그 행을 이어받아야 두 줄이 안 됩니다."""
    grid = _sheet({"A": "9999", "C": "남의 행"}, {"A": "1108", "C": "서울대학교"})
    plan = plan_tab(CLIENTS, grid, [_client_row(_client(1108))], {"1108"})

    assert [entry["range"] for entry in plan.entered] == [
        "'고객 기본 정보'!A3:A3",
        "'고객 기본 정보'!I3:I3",
    ]
    assert plan.clears == []


def test_a_row_the_console_no_longer_has_is_cleared_but_formulas_survive():
    grid = _sheet({"A": "2102", "C": "집나간 햄지"})
    plan = plan_tab(CLIENTS, grid, [], {"2102"})

    # B·G(수식)와 D(Website URL)·H(최초 연락일)는 콘솔이 안 쓰므로 그대로 둡니다.
    assert plan.clears == [
        "'고객 기본 정보'!A2:A2",
        "'고객 기본 정보'!C2:C2",
        "'고객 기본 정보'!E2:F2",
        "'고객 기본 정보'!I2:J2",
    ]
    assert plan.entered == [] and plan.raw == []


def test_rows_past_the_formula_block_are_reported_not_silently_dropped():
    """수식이 깔린 마지막 행을 넘기면 조용히 사라지지 않고 세어서 돌려줍니다."""
    rows = [_client_row(_client(1000 + n)) for n in range(won_sheets.MAX_ROW + 5)]
    plan = plan_tab(CLIENTS, {}, rows, set())

    assert plan.dropped == len(rows) - (won_sheets.MAX_ROW - 1)
