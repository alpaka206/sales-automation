"""수주 고객 — 저장하지 않고 계산하는 값들과, 고객을 하나로 묶는 규칙.

여기서 고정하는 것은 세 가지입니다:

1. **Client ID 는 고객사 하나에 하나.** 전에는 문의 하나에 하나여서, 같은 회사가 두 번
   문의하면 계약과 크레딧과 소통 히스토리가 두 갈래로 갈라졌습니다.
2. **크레딧은 계산 결과**입니다. 운영자가 쓰던 시트의 숫자와 같은 값이 나와야 합니다.
3. **Won 은 한 곳에서만 감지**합니다 — 웹훅·폴러·수동 최신화가 지나는 그 함수.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

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
def test_unit_price_matches_the_operators_own_sheet():
    """실제 계약 두 건으로 검산합니다 — 방향이 뒤집혔습니다.

    받는 것은 금액과 크레딧이고, 분당 단가가 그 둘에서 나옵니다: 금액 ÷ (크레딧 ÷ 60).
    예전에는 단가를 받아 크레딧을 계산했는데, 반올림한 단가로 계산한 크레딧이 계약서의
    크레딧과 어긋났습니다.

    집나간 햄지: 공급가 1,566,000원 · 64,800 크레딧 → 1,450원/분. 시트와 같습니다.
    서울대학교: 20,000,000원 · 456,120 크레딧 → 2,631.xx 원/분 — 딱 떨어지지 않으므로
    소수점을 남깁니다. 반올림하면 되짚어 곱했을 때 금액이 안 맞습니다.
    """
    krw = ClientContract(
        client_id=1, seq=1, currency="KRW", amount_excl_vat=1_566_000, credits=64_800
    )
    assert won.unit_price(krw) == Decimal("1450")

    seoul = ClientContract(
        client_id=2, seq=1, currency="KRW", amount_excl_vat=20_000_000, credits=456_120
    )
    assert round(float(won.unit_price(seoul)), 2) == 2630.89

    # USD 계약은 총액이 기준입니다 — 부가세가 없어 총액이 곧 대금입니다.
    usd = ClientContract(
        client_id=3, seq=1, currency="USD", amount_incl_vat=20_000, credits=60_000
    )
    assert won.unit_price(usd) == Decimal("20")

    # 크레딧이 없으면 계산하지 않습니다. 0 을 넣으면 나눗셈이 터집니다.
    assert won.unit_price(ClientContract(client_id=4, seq=1, currency="KRW",
                                         amount_excl_vat=1_000_000, credits=None)) is None
    assert won.unit_price(ClientContract(client_id=5, seq=1, currency="KRW",
                                         amount_excl_vat=None, credits=100)) is None


def test_the_total_is_the_supply_plus_vat_for_krw_only():
    """원화 계약은 공급가만 받고 총액은 +10% 로 계산합니다 — 입력 칸이 없습니다.
    그 외 통화는 부가세가 없어 총액만 받고 공급가 칸이 없습니다."""
    krw = ClientContract(client_id=1, seq=1, currency="KRW", amount_excl_vat=10_000_000)
    assert won.total_amount(krw) == Decimal("11000000.0")
    assert won.billing_amount(krw) == Decimal("10000000")

    usd = ClientContract(client_id=2, seq=1, currency="USD", amount_incl_vat=20_000)
    assert won.total_amount(usd) == Decimal("20000")
    assert won.billing_amount(usd) == Decimal("20000")


def test_customer_type_comes_from_the_id_band():
    """고객 종류를 따로 저장하지 않는 이유 — 번호대가 곧 종류이고, 둘을 저장하면 어긋납니다."""
    assert won.client_type(1108) == "GTM Inbound"
    assert won.client_type(2102) == "GTM Outbound"
    assert won.client_type(3001) == "Interactive"
    assert won.client_type(4001) == "AX"
    assert won.client_type(9001) == "2025 Inbound"


def test_monthly_revenue_matches_the_sheet():
    """MRR = VAT 포함 총액 ÷ 계약 개월수. PoC 는 결제월에 전액이라 월간 매출이 없습니다."""
    contract = ClientContract(
        client_id=1, seq=1, deal_type="MRR",
        starts_on="2026-06-25", ends_on="2027-06-25",
        # 원화 계약이라 공급가만 저장됩니다 — 총액 22,000,000 은 여기에 10% 를 더한 값.
        currency="KRW", amount_excl_vat=20_000_000,
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
            currency="KRW", amount_excl_vat=10_000_000,   # 총액 11,000,000 은 계산값
            installments=4, first_payment_on="2026-08-01",
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


def test_the_first_credit_date_is_the_form_s_not_the_contract_start(factory):
    """크레딧 첫 지급일은 폼이 받습니다 — 계약 시작일과 다른 계약이 흔합니다.

    세팅 기간을 두고 다음 달 1일부터 주는 식입니다. 계약 시작일에 묶어 두면 운영자가 회차를
    전부 열어 날짜를 하나씩 고쳐야 하고, 목업에도 「첫 지급 예정일」 칸이 있습니다.
    """
    from src.api.routes.won_customers import _seed_schedules

    with factory() as session:
        session.add(Client(client_id=2104, company="테스트"))
        contract = ClientContract(
            client_id=2104, seq=1, starts_on="2026-08-01", ends_on="2027-08-01",
            amount_incl_vat=1_200_000, installments=1, first_payment_on="2026-08-01",
            credits=1200,
        )
        session.add(contract)
        session.flush()
        _seed_schedules(session, contract, credit_rounds=3, first_credit_on="2026-09-15")
        session.commit()

        grants = sorted(contract.credit_grants, key=lambda g: g.no)
        assert [g.grant_on for g in grants] == ["2026-09-15", "2026-10-15", "2026-11-15"]

        # 안 주면 예전대로 계약 시작일부터입니다.
        other = ClientContract(
            client_id=2104, seq=2, starts_on="2026-08-01", ends_on="2027-08-01", credits=100,
        )
        session.add(other)
        session.flush()
        _seed_schedules(session, other, credit_rounds=2)
        assert sorted(g.grant_on for g in other.credit_grants) == ["2026-08-01", "2026-09-01"]


def test_the_mockup_css_does_not_claim_the_console_button_variants():
    """목업의 일반 버튼 규칙이 `btn--danger` 같은 **콘솔 변형**까지 가져가면 안 됩니다.

    `.won button`(0,1,1)·`.won .btn`(0,2,0) 이 `.btn--danger`(0,1,0) 를 특이도로 이겨서,
    `.won` 안에서 연 확인 창의 `삭제` 가 `취소` 와 배경·글자색·테두리·굵기까지 똑같은 흰
    버튼이 됐습니다 — 되돌릴 수 없는 동작에서 빨간색이 사라집니다. 목업 버튼은 줄표가
    하나(`btn-primary`)고 콘솔은 둘(`btn--primary`)이라, 줄표 둘을 빼면 서로 안 겹칩니다.

    `.won .modal` 이 공용 모달을 숨겼던 것과 같은 부류입니다 —
    [[test_the_mockup_css_does_not_own_a_modal_of_its_own]] 을 함께 보세요.
    """
    import pathlib
    import re

    css = pathlib.Path("src/api/static/won.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # 변형이 정하는 것 — 이 넷 중 하나라도 건드리면 변형이 안 보입니다. `cursor` 나 `width`
    # 처럼 색과 무관한 속성까지 막으면, 목업이 버튼에 아무 규칙도 못 쓰게 됩니다.
    looks = ("background", "color", "border", "font-weight")
    for rule in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        selector, body = rule[0].strip(), rule[1]
        if not re.match(r"^\.won (button|\.btn)(?![\w-])", selector):
            continue
        if not any(prop in body for prop in looks):
            continue
        assert "btn--" in selector, selector


def test_the_claim_list_does_not_follow_the_contract_selector():
    """계약을 고르면 그 아래가 전부 바뀝니다 — **클레임만 빼고**.

    클레임은 고객이 겪은 일이지 계약 회차의 일이 아닙니다. 1차 때 난 품질 이슈는 2차를 보고
    있어도 그 고객의 이력이고, 계약을 바꿀 때마다 사라지면 "이 고객이 무슨 일을 겪었나" 를
    회차마다 눌러 봐야 합니다. 저장은 계약에 딸려 있습니다(어느 계약 기간의 일인지가
    정보라서) — 보여줄 때만 전부 모으고, 어느 차수 건인지 뱃지로 적습니다.

    같은 섹션의 갱신 계획·비고는 반대로 계약의 값이라 따라갑니다.
    """
    import pathlib

    screen = pathlib.Path("frontend/src/screens/won/WonCustomerDetail.tsx").read_text(encoding="utf-8")
    # 클레임 섹션은 **계약 목록 전체**를 받습니다. `current` 하나만 받으면 따라가게 됩니다.
    assert "<CareSection contracts={contracts} current={current}" in screen
    assert "contracts\n    .slice().reverse()\n    .flatMap((c) => c.claims" in screen


def test_the_contract_notes_remount_when_the_contract_changes():
    """계약의 값을 `useState` 의 **초기값**으로 받는 컴포넌트는 key 를 달아야 합니다.

    React 는 같은 자리의 같은 컴포넌트를 재사용하므로 초기값을 다시 읽지 않습니다. 1차를
    골랐는데 갱신 계획·비고에 2차의 값이 남아 있었고, 그 상태로 저장을 누르면 1차 계약에
    2차의 값이 덮입니다 — 화면에는 저장됐다고 나옵니다.
    """
    import pathlib

    screen = pathlib.Path("frontend/src/screens/won/WonCustomerDetail.tsx").read_text(encoding="utf-8")
    assert "<ContractNotes key={current.id}" in screen


def test_the_list_screen_keeps_the_mockups_thresholds_and_wording():
    """목록의 임계값·문구는 목업(`수주관리목업_0806.html`)에서 온 것입니다.

    눈에 안 보이는 숫자라 조용히 어긋납니다. 실제로 어긋나 있었습니다:

    - **임박 강조는 14일**입니다(`dueClass`). 7일로 좁히면 다음 주에 할 일이 회색으로
      묻혀, 월요일에 한 번 훑는 화면이 못 됩니다.
    - **지난 것은 `3일 지연`** 이라 씁니다(`dday`). `D+3` 과 `D-3` 은 부호 하나 차이라
      훑을 때 뒤집혀 읽힙니다.
    - **같은 상태 안에서는 종료일이 빠른 순**입니다. 가나다순은 손이 먼저 가야 하는 것을
      알려주지 않습니다.
    - **검색은 담당부서·고객 종류까지** 봅니다. 힌트에 안 적혀 있어도 "GTM" 이나
      "Inbound" 로 찾는 사람은 반드시 있고, 안 걸리면 목록이 빈 것처럼 보입니다.

    환율 줄만 일부러 다릅니다 — 목업은 손으로 적는 칸이고, 여기는 조회한 값과 실제
    고시일을 적습니다(그렇지 않으면 두 사람이 다른 환율로 다른 MRR 을 봅니다).
    """
    import pathlib

    shared = pathlib.Path("frontend/src/screens/won/shared.ts").read_text(encoding="utf-8")
    assert "left <= 14 ? \"due\"" in shared
    assert '`${-left}일 지연`' in shared
    assert '"오늘"' in shared

    screen = pathlib.Path("frontend/src/screens/won/WonCustomers.tsx").read_text(encoding="utf-8")
    assert "row.department, row.customer_type" in screen
    assert 'a.active?.ends_on ?? "9999-12-31"' in screen
    # 환율은 조회값 — 손으로 적는 칸으로 되돌리면 안 됩니다.
    assert 'fx_on' in screen
    assert 'id="fxInput"' not in screen


def test_the_form_asks_for_the_amount_the_currency_uses():
    """통화가 어느 칸을 받는지 정합니다 — 원화는 공급가, 그 외는 총액. 둘 다 받으면
    분당 단가가 어느 쪽 기준인지 계약마다 달라집니다."""
    import pathlib

    form = pathlib.Path("frontend/src/screens/won/WonContractForm.tsx").read_text(encoding="utf-8")

    assert '<Field label="공급가 (VAT 제외)" required>' in form
    # 원화면 총액 칸은 읽기 전용 계산값, USD 면 총액이 입력이고 공급가 칸이 없습니다.
    assert '{totalInclVat === null ? "공급가 입력 시 계산" : num(totalInclVat)}' in form
    assert '<label className="form-label">총 계약금액 (VAT 포함) <span className="req">*</span></label>' in form
    # 저장을 막는 조건은 통화가 정한 금액과 크레딧, 둘뿐입니다.
    guard = form[form.index("const [save, saving]") : form.index("const body: Record")]
    assert "billing" in guard and "draft.credits" in guard
    assert "unit_price" not in guard


def test_the_unit_price_is_shown_not_typed():
    """분당 단가는 계산값입니다. 입력 칸으로 두면 반올림한 단가로 계산한 크레딧이
    계약서의 크레딧과 어긋납니다 — 그래서 방향을 뒤집었습니다."""
    import pathlib

    from src.db.models import ClientContract

    form = pathlib.Path("frontend/src/screens/won/WonContractForm.tsx").read_text(encoding="utf-8")
    assert 'set("unit_price"' not in form          # 입력하지 않습니다
    assert 'set("credits"' in form                 # 크레딧은 입력합니다
    # 단가 통화·적용 환율 칸은 사라졌습니다.
    assert "unit_currency" not in form
    assert "unit_fx_rate" not in form

    # 행에도 없습니다 — 계산값을 저장하면 갈라집니다.
    columns = {c.name for c in ClientContract.__table__.columns}
    assert "unit_price" not in columns
    assert "unit_currency" not in columns
    assert "unit_fx_rate" not in columns


def test_the_sheet_gets_the_computed_amounts():
    """시트 O(분당 단가)·L(총액)도 계산값이 나가고, 없어진 칸(N·P)은 비웁니다."""
    import pathlib

    source = pathlib.Path("src/agents/won_sheets.py").read_text(encoding="utf-8")
    assert '"O": _num(won.unit_price(contract))' in source
    assert '"L": _num(won.total_amount(contract))' in source
    assert '"N": "",' in source and '"P": "",' in source
    # 시트에서 거꾸로 읽어 오지도 않습니다.
    importer = pathlib.Path("src/agents/sheet_to_db.py").read_text(encoding="utf-8")
    assert "contract.unit_price" not in importer


# ----- 플랜 상태는 계약 기간이 정합니다 — 저장하지 않습니다 -----


def _client_with(*periods, today=None):
    """(시작일, 종료일) 쌍으로 계약을 단 고객. None 은 날짜가 덜 적힌 계약입니다."""
    from types import SimpleNamespace

    return SimpleNamespace(contracts=[
        SimpleNamespace(starts_on=start, ends_on=end) for start, end in periods
    ])


def test_plan_status_follows_the_contract_dates():
    from datetime import date

    from src.common.won import plan_status

    today = date(2026, 8, 11)
    running = ("2026-08-01", "2027-08-01")
    ended = ("2024-01-01", "2025-01-01")
    upcoming = ("2026-12-01", "2027-12-01")

    # 계약 기간이 끝나면 손대지 않아도 사용 중단으로 갑니다 — 이것이 요구의 핵심입니다.
    assert plan_status(_client_with(ended), today) == "사용 중단"
    assert plan_status(_client_with(running), today) == "사용중"
    # 추가된 계약이 있으면 세팅중. 지난 계약이 같이 있어도 마찬가지입니다.
    assert plan_status(_client_with(upcoming), today) == "세팅중"
    assert plan_status(_client_with(ended, upcoming), today) == "세팅중"
    # 진행 중인 계약이 하나라도 있으면 사용중이 이깁니다.
    assert plan_status(_client_with(running, upcoming), today) == "사용중"
    # 날짜가 덜 적힌 계약("작성중")도 세팅중입니다 — contract_state 와 다른 점입니다.
    assert plan_status(_client_with((None, None)), today) == "세팅중"
    assert plan_status(_client_with(("2026-08-01", None)), today) == "세팅중"
    # 계약이 아직 없는 고객(수주 전환만 된 상태)도 세팅중.
    assert plan_status(_client_with(), today) == "세팅중"


def test_plan_status_is_not_stored_anywhere():
    """열로 들고 있으면 반드시 어긋납니다 — 계약이 끝나도 「사용중」이 남던 것이 그 증상입니다.
    고객 종류를 저장하지 않는 것과 같은 이유입니다."""
    import pathlib

    from src.db.models import Client

    assert "plan_status" not in {c.name for c in Client.__table__.columns}

    # 화면에도 고르개가 없어야 합니다 — 사람이 고른 값과 날짜가 말하는 값이 갈라집니다.
    for path in (
        "frontend/src/screens/won/WonCustomerDetail.tsx",
        "frontend/src/screens/won/WonContractForm.tsx",
    ):
        source = pathlib.Path(path).read_text(encoding="utf-8")
        assert 'set("plan_status"' not in source, path
        assert "onChange={setPlanStatus}" not in source, path


def test_the_sheet_gets_the_same_status_the_screen_shows():
    """시트 J열도 같은 함수에서 나옵니다. 저장된 값을 싣던 시절에는 계약이 끝나도 시트가
    「사용중」인 채였습니다."""
    import pathlib

    source = pathlib.Path("src/agents/won_sheets.py").read_text(encoding="utf-8")
    assert '"J": _text(won.plan_status(client))' in source
    # 시트에서 거꾸로 읽어 오지도 않습니다 — 손으로 적힌 옛 값이 날짜를 이기면 안 됩니다.
    importer = pathlib.Path("src/agents/sheet_to_db.py").read_text(encoding="utf-8")
    assert "client.plan_status" not in importer
