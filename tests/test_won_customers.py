"""수주 고객 — 저장하지 않고 계산하는 값들과, 고객을 하나로 묶는 규칙.

여기서 고정하는 것은 세 가지입니다:

1. **Client ID 는 고객사 하나에 하나.** 전에는 문의 하나에 하나여서, 같은 회사가 두 번
   문의하면 계약과 크레딧과 소통 히스토리가 두 갈래로 갈라졌습니다.
2. **크레딧은 계산 결과**입니다. 운영자가 쓰던 시트의 숫자와 같은 값이 나와야 합니다.
3. **Won 은 한 곳에서만 감지**합니다 — 웹훅·폴러·수동 최신화가 지나는 그 함수.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common import won
from src.db.base import Base
from src.db.models import Client, ClientContract, Contact, ContractCreditGrant, ContractPayment


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


# --------------------------------------------------------------------------- #
# 크레딧 — 운영자의 시트와 같은 숫자가 나와야 합니다
# --------------------------------------------------------------------------- #
def test_credits_match_the_operators_own_sheet():
    """실제 계약 두 건으로 검산합니다.

    집나간 햄지: 공급가 1,566,000원 ÷ 1,450원/분 × 60 = 64,800 크레딧 — 시트와 정확히 같습니다.

    서울대학교는 통화가 갈립니다(원화 계약, USD 단가). 시트의 456,120 크레딧이 나오려면
    환율이 **1,503.36** 이어야 합니다 — 계약 시점의 값이고, 오늘 값이 아닙니다. 그래서
    계약 행에 박아 둡니다: 1,503 으로 반올림만 해도 456,230 이 되어 110 크레딧(약 2분)이
    어긋나고, 오늘 환율(1,380)로 계산하면 아예 다른 숫자가 됩니다.
    """
    assert won.contract_credits(1_566_000, 1450, "KRW", "KRW", None) == 64_800
    assert won.contract_credits(20_000_000, "1.75", "KRW", "USD", "1503.3637") == 456_120
    assert won.contract_credits(20_000_000, "1.75", "KRW", "USD", 1503) == 456_230
    # 환율이 없으면 계산하지 않습니다. 0 이나 추정값을 넣으면 틀린 크레딧이 조용히 저장됩니다.
    assert won.contract_credits(20_000_000, 1.75, "KRW", "USD", None) is None


def test_customer_type_comes_from_the_id_band():
    """고객 종류를 따로 저장하지 않는 이유 — 번호대가 곧 종류이고, 둘을 저장하면 어긋납니다."""
    assert won.client_type(1108) == "Inbound"
    assert won.client_type(2102) == "GTM Outbound"
    assert won.client_type(3001) == "Interactive"
    assert won.client_type(4001) == "AX"
    assert won.client_type(9001) == "2025 Inbound"


def test_monthly_revenue_matches_the_sheet():
    """MRR = VAT 포함 총액 ÷ 계약 개월수. PoC 는 결제월에 전액이라 월간 매출이 없습니다."""
    contract = ClientContract(
        client_id=1, seq=1, deal_type="MRR",
        starts_on="2026-06-25", ends_on="2027-06-25", amount_incl_vat=22_000_000,
    )
    assert won.months_between(contract.starts_on, contract.ends_on) == 12
    assert round(float(won.monthly_revenue(contract))) == 1_833_333
    contract.deal_type = "PoC"
    assert won.monthly_revenue(contract) == 0


def test_contract_state_is_read_from_today_not_stored():
    today = date(2026, 8, 6)
    running = ClientContract(client_id=1, seq=1, starts_on="2026-06-25", ends_on="2027-06-25")
    upcoming = ClientContract(client_id=1, seq=2, starts_on="2026-09-01", ends_on="2027-08-31")
    ended = ClientContract(client_id=1, seq=3, starts_on="2025-01-01", ends_on="2026-01-01")
    assert won.contract_state(running, today) == "진행 중"
    assert won.contract_state(upcoming, today) == "세팅중"
    assert won.contract_state(ended, today) == "종료"


def test_next_dates_are_the_earliest_unfinished_round():
    contract = ClientContract(client_id=1, seq=1)
    contract.credit_grants = [
        ContractCreditGrant(no=1, total=3, grant_on="2026-06-01", amount=100, done=True),
        ContractCreditGrant(no=3, total=3, grant_on="2026-10-01", amount=100),
        ContractCreditGrant(no=2, total=3, grant_on="2026-08-01", amount=100),
    ]
    contract.payments = [
        ContractPayment(no=1, total=2, paid_on="2026-06-01", amount=1, done=True),
        ContractPayment(no=2, total=2, paid_on="2026-09-01", amount=1),
    ]
    # 회차 번호가 아니라 **날짜**로 고릅니다 — 순서가 뒤섞여 저장돼도 다음 날짜는 하나입니다.
    assert won.next_credit_grant(contract).grant_on == "2026-08-01"
    assert won.next_payment(contract).paid_on == "2026-09-01"


def test_a_weekend_payment_uses_the_friday_rate():
    """환율은 영업일에만 고시됩니다. 토·일 입금은 직전 금요일 값입니다."""
    assert won.previous_business_day(date(2026, 8, 8)) == date(2026, 8, 7)  # 토 → 금
    assert won.previous_business_day(date(2026, 8, 9)) == date(2026, 8, 7)  # 일 → 금
    assert won.previous_business_day(date(2026, 8, 7)) == date(2026, 8, 7)  # 금은 그대로


# --------------------------------------------------------------------------- #
# Client ID — 고객사 하나에 하나
# --------------------------------------------------------------------------- #
def test_the_same_company_keeps_one_client_id(factory):
    """담당자가 바뀌어 다른 사람이 문의해도 같은 고객입니다.

    이게 깨지면 한 고객의 계약·크레딧·소통 히스토리가 두 Client 로 갈라지고, 나중에
    합치는 것은 훨씬 비쌉니다.
    """
    from src.agents.client_ids import find_existing_client_id

    with factory() as session:
        first = Contact(normalized_email="a@acme.com", email="a@acme.com",
                        full_name="첫 담당자", domain="acme.com", sheet_client_id=1108)
        second = Contact(normalized_email="b@acme.com", email="b@acme.com",
                         full_name="다음 담당자", domain="acme.com")
        session.add_all([first, second])
        session.commit()
        assert find_existing_client_id(session, second) == 1108


def test_two_gmail_senders_are_not_one_company(factory):
    """개인 메일 도메인으로 묶으면 남의 계약이 보입니다."""
    from src.agents.client_ids import find_existing_client_id

    with factory() as session:
        one = Contact(normalized_email="x@gmail.com", email="x@gmail.com",
                      full_name="X", domain="gmail.com", sheet_client_id=1200)
        two = Contact(normalized_email="y@gmail.com", email="y@gmail.com",
                      full_name="Y", domain="gmail.com")
        session.add_all([one, two])
        session.commit()
        assert find_existing_client_id(session, two) is None


def test_each_band_allocates_its_own_numbers(factory):
    """2000/3000/4000 번대는 콘솔이 발급합니다. 이미 쓰는 번호를 다시 내주면 안 됩니다."""
    from src.agents.client_ids import next_client_id

    with factory() as session:
        session.add(Client(client_id=2102, company="집나간 햄지"))
        session.commit()
        assert next_client_id(session, "GTM Outbound") == 2103
        assert next_client_id(session, "Interactive") == 3001
        with pytest.raises(ValueError):
            next_client_id(session, "2025 Inbound")  # 레거시 — 새로 만들지 않습니다


# --------------------------------------------------------------------------- #
# Won 감지
# --------------------------------------------------------------------------- #
def test_won_lands_in_the_waiting_list_once(factory, monkeypatch):
    """웹훅·폴러·수동 최신화가 전부 이 함수를 지납니다. 같은 티켓이 두 번 와도 한 줄입니다."""
    from src.agents import stage_sync
    from src.db.models import Conversation, PendingWon

    with factory() as session:
        contact = Contact(normalized_email="c@acme.com", full_name="담당", company="ACME")
        session.add(contact)
        session.flush()
        session.add(Conversation(contact_id=contact.id, hubspot_ticket_id="T-1",
                                 stage="negotiation", sheet_client_id=1108))
        session.commit()

    monkeypatch.setattr(stage_sync, "SessionLocal", factory)
    monkeypatch.setattr(stage_sync, "local_stage_for", lambda _stage: "won")
    monkeypatch.setattr(stage_sync, "_mirror_stage_to_sheet", lambda *a, **k: None)

    assert stage_sync.sync_stage_from_hubspot("T-1", "won-stage-id") == "won"
    # 두 번째는 단계가 이미 won 이라 아무 일도 없습니다.
    assert stage_sync.sync_stage_from_hubspot("T-1", "won-stage-id") is None

    with factory() as session:
        rows = session.query(PendingWon).all()
        assert [(r.ticket_id, r.client_id, r.status) for r in rows] == [("T-1", 1108, "pending")]


def test_the_console_can_actually_reach_the_write_routes():
    """`/won-customers` 는 화면이 세션 쿠키로 부르는 브라우저 경로입니다.

    security 의 목록에 없으면 토큰을 요구해서, 로그인한 운영자가 "invalid or missing
    token" 을 받습니다 — 실제로 그렇게 막혀 있었고, 화면에는 저장 실패로만 보입니다.
    """
    from src.api.security import is_web_ui_path

    for path in ("/won-customers", "/won-customers/2102/contracts", "/api/ui/won-customers"):
        assert is_web_ui_path(path), path


def test_credit_rounds_add_up_to_the_contract(factory):
    """회차로 나눌 때 나머지는 **마지막 회차**에 붙습니다.

    회차마다 반올림하면 합계가 계약 크레딧과 어긋나는데, 그 차이는 화면 어디에도 안 보이고
    "누적 지급 = 계약 크레딧" 이라는 검증만 조용히 실패합니다.
    """
    from src.api.routes.won_customers import _seed_schedules

    with factory() as session:
        session.add(Client(client_id=2103, company="테스트"))
        contract = ClientContract(
            client_id=2103, seq=1, starts_on="2026-08-01", ends_on="2027-08-01",
            amount_incl_vat=11_000_000, installments=4, first_payment_on="2026-08-01",
            credits=240_817,
        )
        session.add(contract)
        session.flush()
        _seed_schedules(session, contract, credit_rounds=3)
        session.commit()

        grants = sorted(contract.credit_grants, key=lambda g: g.no)
        assert [g.amount for g in grants] == [80_272, 80_272, 80_273]
        assert sum(g.amount for g in grants) == 240_817
        assert [g.grant_on for g in grants] == ["2026-08-01", "2026-09-01", "2026-10-01"]
        # 분납은 총액을 나눈 값. 합계가 총 계약금액이어야 합니다.
        assert sum(float(p.amount) for p in contract.payments) == 11_000_000


def test_every_write_route_answers_post():
    """콘솔의 쓰기 헬퍼는 POST 하나만 보냅니다.

    라우트가 PUT 이면 405 가 나고 화면에는 "저장이 안 된다" 로만 보입니다 — 크레딧 지급
    완료가 실제로 그렇게 막혀 있었습니다. 동사를 둘 두면 어느 쪽인지 매번 확인해야 하고,
    그 확인을 한 번 빠뜨리면 같은 일이 반복됩니다.
    """
    from src.api.routes import won_customers

    for route in won_customers.router.routes:
        methods = set(getattr(route, "methods", ()))
        if methods <= {"GET", "HEAD"}:
            continue  # CSV 내보내기
        assert methods == {"POST"}, (route.path, methods)


def test_the_mockup_css_does_not_own_a_modal_of_its_own():
    """모달 껍데기는 콘솔의 `Modal` 한 곳에서만 그립니다.

    목업에는 `.scrim` + `.modal.is-open` 이라는 제 나름의 모달이 있었습니다. 포팅하면서
    같이 들어왔는데, `.won` 안에서 콘솔 모달을 열면 그 규칙이 그대로 물었습니다 — 공용
    `Modal` 은 `is-open` 을 **배경**에 붙이므로 `.won .modal` 은 `.is-open` 없이 남고,
    `opacity:0; pointer-events:none` 이 됩니다. DOM 에는 있는데 화면에는 없고, 클릭도
    안 먹습니다. 계약 추가가 "번쩍 했다가 사라지던" 것이 이것이었습니다.

    안쪽 모양(`.modal-head` · `.modal-body` · `.modal-foot`)은 목업 것을 그대로 씁니다.
    """
    import pathlib
    import re

    css = pathlib.Path("src/api/static/won.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)  # 왜 지웠는지 적어 둔 주석은 빼고
    # `.won .modal-body` 같은 안쪽 규칙은 두고, `.modal` 자체를 잡는 선택자만 막습니다.
    hijacks = re.findall(r"^\.won \.modal(?![\w-])[^{]*\{", css, re.MULTILINE)
    assert not hijacks, hijacks
    assert ".scrim" not in css
