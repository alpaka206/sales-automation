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


def test_the_console_does_not_manage_claims_at_all():
    """클레임은 콘솔 밖에서 관리합니다(운영자 지시, 2026-08-13).

    화면만 숨기면 아무도 안 보는데 계속 동기화되는 테이블이 남고, 다음 사람이 열어 보고
    "이건 왜 비어 있지" 를 확인하러 갑니다. 그래서 표·라우트·파생값·시트 동기화·테이블까지
    전부 지웠습니다(마이그레이션 0072). **시트의 탭은 그대로 둡니다** — 시트는 운영자의
    것이고, 콘솔이 안 건드리므로 손으로 적는 자리로 남습니다.
    """
    import pathlib

    from src.common import won
    from src.db import models

    assert not hasattr(models, "ContractClaim")
    assert not hasattr(won, "open_claims")
    assert not hasattr(won, "CLAIM_PROGRESS")
    assert "claims" not in {r.key for r in models.ClientContract.__mapper__.relationships}

    for name in (
        "frontend/src/screens/won/WonCustomerDetail.tsx",
        "frontend/src/screens/won/WonCustomers.tsx",
        "frontend/src/screens/won/shared.ts",
        "src/api/routes/won_customers.py",
        "src/api/routes/ui_api.py",
    ):
        source = pathlib.Path(name).read_text(encoding="utf-8")
        # 주석에 "지웠다" 고 적는 것은 되므로, 코드가 쓰는 이름만 봅니다.
        assert "ContractClaim" not in source, name
        assert "open_claims" not in source, name
        assert "contract.claims" not in source, name


def test_the_console_does_not_manage_renewal_notes_at_all():
    """「갱신 · 비고」도 콘솔 밖으로 나갔습니다(운영자 지시, 2026-08-14 · 이관 0073).

    클레임과 같은 이유입니다 — 화면만 지우면 아무도 안 보는데 계속 동기화되는 열이 남습니다.
    그래서 패널·라우트 필드·CSV 열·선택지·시트 동기화·모델 열까지 전부 지웠습니다.
    **워크북의 W·X·Y 열은 그대로 둡니다** — 콘솔이 안 건드리므로 손으로 적는 자리가 됩니다.
    그래서 `owned` 에서도 빠져야 합니다: 남겨 두면 콘솔이 지운 고객의 행에서 그 세 칸까지
    같이 비웁니다.
    """
    import pathlib

    from src.agents.won_sheets import CONTRACTS
    from src.common import won
    from src.db import models

    columns = set(models.ClientContract.__mapper__.columns.keys())
    assert not columns & {"renewal_plan", "stop_reason", "memo"}
    assert not hasattr(won, "RENEWAL_PLANS")
    assert not set(CONTRACTS.owned) & {"W", "X", "Y"}

    for name in (
        "frontend/src/screens/won/WonCustomerDetail.tsx",
        "frontend/src/screens/won/WonContractForm.tsx",
        "frontend/src/screens/won/shared.ts",
        "src/api/routes/won_customers.py",
        "src/api/routes/ui_api.py",
        "src/agents/sheet_to_db.py",
    ):
        source = pathlib.Path(name).read_text(encoding="utf-8")
        # 주석에 "지웠다" 고 적는 것은 되므로, 코드가 쓰는 철자만 봅니다.
        assert "contract.renewal_plan" not in source, name
        assert "contract.stop_reason" not in source, name
        assert "contract.memo" not in source, name
        assert "sec-care" not in source, name


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


def test_the_form_asks_for_the_amount_the_vat_answer_uses():
    """**부가세 해당 여부**가 어느 칸을 받는지 정합니다 — 통화가 아니라(이관 0075).

    해당이면 포함·미포함 두 칸을 다 받고 한쪽을 적으면 다른 쪽이 따라옵니다: 계약서가 어느
    쪽으로 적혀 있든 그 숫자를 그대로 넣을 수 있어야 합니다. 미해당이면 금액은 하나입니다.
    """
    import pathlib

    form = pathlib.Path("frontend/src/screens/won/WonContractForm.tsx").read_text(encoding="utf-8")

    assert '<Field label="VAT 해당 여부">' in form
    assert '<Field label="총 계약금액 (VAT 포함)" required>' in form
    assert '<Field label="공급가 (VAT 미포함)" required>' in form
    # 한쪽을 적으면 다른 쪽이 10% 로 따라옵니다.
    assert 'setAmount("incl"' in form and 'setAmount("excl"' in form
    # 미해당은 한 칸이고, 「VAT 포함」이라는 말을 쓰지 않습니다.
    assert '<label className="form-label">계약금액 <span className="req">*</span></label>' in form
    # 저장을 막는 조건은 금액과 크레딧, 둘뿐입니다.
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


def test_an_old_krw_contract_with_only_a_total_still_opens_and_saves():
    """공급가를 받기로 하기 전의 원화 계약은 **총액만** 채워져 있습니다.

    그대로 두면 화면의 공급가 칸이 비고, 필수 칸이라 그 계약은 플랜 하나 고치는 것조차
    저장이 막힙니다 — 요청이 아예 안 나가고, 누른 사람에게는 "플랜이 적용이 안 된다" 로만
    보입니다. 총액 = 공급가 + 10% 의 정확한 역이라 되짚어도 값을 지어내지 않습니다.
    """
    old = ClientContract(
        client_id=1, seq=1, currency="KRW", amount_incl_vat=1_722_600, credits=64_800
    )
    assert won.billing_amount(old) == Decimal("1566000")
    assert won.total_amount(old) == Decimal("1722600")     # 되짚어도 원래 총액
    assert won.unit_price(old) == Decimal("1450")          # 시트와 같은 단가

    # 공급가가 있으면 그쪽이 원본입니다 — 되짚기는 없을 때만.
    both = ClientContract(
        client_id=2, seq=1, currency="KRW",
        amount_excl_vat=1_000_000, amount_incl_vat=9_999_999, credits=60_000,
    )
    assert won.billing_amount(both) == Decimal("1000000")


def test_saving_only_the_notes_never_erases_the_money(factory):
    """이 라우트에는 계약 전체 폼만 오는 게 아닙니다.

    `_fill_contract` 는 폼에 온 칸만 건드리므로(`if name in form`) 몇 칸만 보내는 폼도
    받습니다. 실제로 「갱신 계획·사용 중단 이유·비고」 패널이 세 칸만 보냈고(0073 에서
    없어졌습니다), 그때 통화가 안 쓰는 금액 칸을 조건 없이 비우니 **총액만 있던 옛 원화
    계약은 비고 한 줄 저장에 금액이 통째로 사라졌습니다** — 되돌릴 방법이 없습니다.
    """
    from src.api.routes.won_customers import _fill_contract

    옛계약 = ClientContract(client_id=1, seq=1, currency="KRW",
                          amount_incl_vat=1_722_600, credits=64_800)
    _fill_contract(옛계약, {"note": "통화만 해 봄"})
    assert 옛계약.amount_incl_vat == 1_722_600, "비고 저장에 금액이 사라졌습니다"
    assert won.total_amount(옛계약) == Decimal("1722600")
    assert won.unit_price(옛계약) == Decimal("1450")

    # **두 칸이 갈라진 채로 저장되지 않습니다.** 예전에는 반대쪽을 비웠고, 지금은 고른
    # 기준에서 다시 계산합니다(이관 0075: VAT 해당 계약은 포함·미포함을 둘 다 보여 줍니다).
    # 어느 쪽이든 결론은 같습니다 — 분당 단가와 총액이 서로 다른 금액에서 나오는 상태가
    # 아예 없습니다. 여기서 9,999,999 는 손으로 한쪽만 고친 요청을 흉내 낸 값입니다.
    정상 = ClientContract(client_id=2, seq=1, currency="KRW",
                         amount_excl_vat=1_566_000, amount_incl_vat=9_999_999)
    _fill_contract(정상, {"note": "x"})
    assert 정상.amount_excl_vat == 1_566_000
    assert 정상.amount_incl_vat == Decimal("1722600.0"), "기준(공급가)에서 다시 계산합니다"


def test_a_contract_with_no_vat_keeps_one_amount(factory):
    """부가세가 없는 계약은 금액이 하나입니다 — 그 하나는 `amount_incl_vat` 에 삽니다.

    「포함」이라는 이름이 남아 있는 것은 열 이름을 바꾸는 이관이 살아 있는 금액 열을
    건드리는 일이기 때문입니다. 부가세가 없는 계약에서 그 이름은 그냥 「그 금액」입니다.
    """
    from src.api.routes.won_customers import _fill_contract

    해외 = ClientContract(client_id=3, seq=1, currency="USD", credits=60_000)
    _fill_contract(해외, {"vat_applicable": "", "amount_incl_vat": "20000"})

    assert 해외.amount_incl_vat == Decimal("20000")
    assert 해외.amount_excl_vat is None, "부가세가 없으면 공급가 칸은 비어 있습니다"
    assert won.total_amount(해외) == Decimal("20000")
    assert won.supply_amount(해외) is None


def test_vat_applicability_is_the_customers_not_the_currencys(factory):
    """국내 법인이면 USD 계약이어도 부가세가 붙고, 해외 고객이면 원화여도 안 붙습니다.

    한동안 통화가 이 판단을 대신했습니다(`won.is_krw`). 대부분 맞지만 늘 맞지는 않아서
    계약마다 고르는 칸이 되었습니다(이관 0075).
    """
    from src.api.routes.won_customers import _fill_contract

    국내달러 = ClientContract(client_id=4, seq=1, currency="USD", credits=60_000)
    _fill_contract(국내달러, {"vat_applicable": "1", "vat_included": "", "amount_excl_vat": "10000"})
    assert won.vat_applicable(국내달러) is True
    assert 국내달러.amount_incl_vat == Decimal("11000"), "USD 여도 10% 가 붙습니다"

    해외원화 = ClientContract(client_id=5, seq=1, currency="KRW", credits=60_000)
    _fill_contract(해외원화, {"vat_applicable": "", "amount_incl_vat": "1000000"})
    assert won.vat_applicable(해외원화) is False
    assert won.total_amount(해외원화) == Decimal("1000000"), "원화여도 10% 를 안 더합니다"


def test_a_contract_written_before_the_column_existed_keeps_its_old_meaning(factory):
    """`vat_applicable` 이 비어 있으면 옛 규칙으로 떨어집니다 — 원화면 해당.

    이 칸이 생기기 전의 계약 수백 건에는 고른 값이 없습니다. 없는 것을 「미해당」으로 읽으면
    그 원화 계약들의 총액이 한꺼번에 10% 내려앉습니다.
    """
    옛계약 = ClientContract(client_id=6, seq=1, currency="KRW", amount_excl_vat=1_000_000)
    assert 옛계약.vat_applicable is None
    assert won.vat_applicable(옛계약) is True
    assert won.total_amount(옛계약) == Decimal("1100000")


def test_the_registry_append_never_writes_into_a_formula_column():
    """「고객 기본 정보」의 고객 종류(B)와 담당부서(G)는 둘 다 ARRAYFORMULA 열입니다.

    값으로 쓰면 「배열 결과가 데이터를 덮어쓰게 되어」 그 열 전체가 #REF! 가 되고, 그
    뒤로는 아무 행도 계산하지 않습니다. B 는 비우면서 G 에는 값을 넣고 있었습니다 —
    이 append 를 쓸 때 G 는 아직 손으로 적는 칸이었기 때문입니다.
    """
    import pathlib
    import sys

    sys.argv = ["x"]
    from scripts.build_won_sheets import TABS

    registry = next(tab for tab in TABS if tab["title"] == "고객 기본 정보")
    수식열 = set(registry["array"])
    assert 수식열 == {"B", "G"}, "수식 열이 바뀌었으면 아래 append 도 같이 봐야 합니다"

    source = pathlib.Path("src/integrations/google_sheets.py").read_text(encoding="utf-8")
    block = source[source.index("_REGISTRY_TAB}'!A1"):]
    block = block[: block.index(").execute()")]
    assert "DEPARTMENT_BY_TYPE" not in block, "담당부서(G)는 수식 열입니다 — 값을 쓰면 안 됩니다"


def test_a_failed_rate_lookup_is_retried_not_frozen_for_the_day(monkeypatch):
    """조회 실패를 하루치 캐시에 넣으면 아침에 한 번 삐끗한 것이 그날 내내 「설정값」이
    되고, 프로세스를 재시작하기 전까지 안 풀립니다. 실패하는 이유는 대개 그때뿐인 것
    (콜드 스타트·타임아웃)이라 조금 있다 다시 물어야 합니다."""
    from decimal import Decimal

    from src.integrations import fx

    monkeypatch.setattr(fx, "_today_cache", {})
    monkeypatch.setattr(fx, "_last_attempt", 0.0)
    monkeypatch.setattr(fx, "_last_error", None)

    호출 = []

    def 실패(day):
        호출.append(day)
        fx._remember_error("Frankfurter", day, ConnectionError("망 끊김"))
        return None

    monkeypatch.setattr(fx, "usd_krw_on", 실패)
    assert fx.usd_krw_today() is None
    assert fx.last_error() and "망 끊김" in fx.last_error()

    # 곧바로 다시 부르면 외부를 또 때리지는 않습니다 — 안 되는 날 매 요청이 8초씩 밀립니다.
    assert fx.usd_krw_today() is None
    assert len(호출) == 1

    # 재시도 간격이 지나면 다시 물어보고, 성공하면 그 값이 그날의 값이 됩니다.
    monkeypatch.setattr(fx, "_last_attempt", 0.0)
    monkeypatch.setattr(fx, "usd_krw_on", lambda day: (Decimal("1416.62"), "2026-08-10", "ecb"))
    assert fx.usd_krw_today() == (Decimal("1416.62"), "2026-08-10", "ecb")
    assert fx.last_error() is None          # 성공하면 이유를 지웁니다


# ----- 이번달 예상 MRR — 계약 기간이 정한다 -----


def _계약(deal="MRR", currency="KRW", 회차=(), **kw):
    from src.db.models import ContractPayment

    c = ClientContract(client_id=1, seq=1, deal_type=deal, currency=currency,
                       starts_on="2026-01-01", ends_on="2026-12-31", **kw)
    c.payments = [
        ContractPayment(no=i + 1, total=len(회차), paid_on=on, amount=amt)
        for i, (on, amt) in enumerate(회차)
    ]
    return c


def test_this_months_revenue_is_decided_by_the_contract_period():
    """계약 금액 ÷ 계약 개월수. 결제를 어떻게 받았는지는 안 봅니다.

    한동안 결제일이 정하게 뒀는데, 그러면 12개월 계약을 1월에 일시불로 받은 고객이 2월부터
    카드에서 사라집니다 — 매달 쓰고 있는데도요.
    """
    이번달 = "2026-08"
    한달치 = Decimal("1100000")            # 13,200,000 ÷ 12

    일시불 = _계약(회차=[("2026-01-15", 13_200_000)], amount_excl_vat=12_000_000)
    assert won.revenue_in_month(일시불, 이번달) == 한달치
    assert won.revenue_in_month(일시불, "2026-01") == 한달치

    분할 = _계약(회차=[("2026-02-15", 6_600_000), ("2026-08-20", 6_600_000)],
               amount_excl_vat=12_000_000)
    assert won.revenue_in_month(분할, 이번달) == 한달치      # 회차를 어떻게 나눴든 같습니다

    회차없음 = _계약(회차=[], amount_excl_vat=12_000_000)
    assert won.revenue_in_month(회차없음, 이번달) == 한달치


def test_only_the_months_inside_the_contract_period_count():
    """기간 밖은 0 입니다 — 안 그러면 끝난 계약이 영원히 이번 달 매출에 남습니다."""
    계약 = _계약(회차=[], amount_excl_vat=12_000_000)       # 2026-01 ~ 2026-12
    assert won.revenue_in_month(계약, "2025-12") == 0
    assert won.revenue_in_month(계약, "2026-12") == Decimal("1100000")
    assert won.revenue_in_month(계약, "2027-01") == 0

    # 인식 시작월을 직접 지정하면 거기서부터 셉니다.
    늦게 = _계약(회차=[], amount_excl_vat=12_000_000, revenue_from="2026-03")
    assert won.revenue_in_month(늦게, "2026-01") == 0
    assert won.revenue_in_month(늦게, "2027-02") == Decimal("1100000")


def test_a_poc_lands_whole_in_the_month_of_its_first_payment():
    """PoC 는 분할이든 한 번에든 쪼개지 않습니다 — 첫 회차가 있는 달에 계약 전액."""
    이번달 = "2026-08"

    첫회차가_이번달 = _계약(deal="PoC", 회차=[("2026-08-10", 2_750_000), ("2026-09-10", 2_750_000)],
                      amount_excl_vat=5_000_000)
    assert won.revenue_in_month(첫회차가_이번달, 이번달) == Decimal("5500000.0")
    assert won.revenue_in_month(첫회차가_이번달, "2026-09") == 0    # 이미 8월에 잡혔습니다

    첫회차가_지난달 = _계약(deal="PoC", 회차=[("2026-07-10", 2_750_000), ("2026-08-10", 2_750_000)],
                      amount_excl_vat=5_000_000)
    assert won.revenue_in_month(첫회차가_지난달, 이번달) == 0


def test_a_contract_with_no_amount_or_no_period_is_counted_as_nothing():
    """금액이나 기간이 덜 적힌 계약은 0 입니다 — 채우라는 신호입니다."""
    금액없음 = _계약(회차=[])
    assert won.revenue_in_month(금액없음, "2026-08") == 0

    기간없음 = _계약(회차=[], amount_excl_vat=12_000_000)
    기간없음.starts_on = 기간없음.ends_on = None
    assert won.revenue_in_month(기간없음, "2026-08") == 0

    # PoC 는 그대로 회차가 정합니다 — 균등 배분할 정기 매출이 아닙니다.
    assert won.revenue_in_month(_계약(deal="PoC", 회차=[], amount_excl_vat=5_000_000), "2026-08") == 0


def test_the_card_does_not_filter_by_plan_status():
    """세팅중이든 사용 중단이든 **이번 달이 계약 기간 안이면** 이번 달 돈입니다.

    화면이 행을 걸러 더하던 시절에는 그 필터가 곧 정의였습니다 — 이번 달에 시작한
    세팅중 계약이 카드에서 통째로 빠졌습니다.
    """
    import pathlib

    screen = pathlib.Path("frontend/src/screens/won/WonCustomers.tsx").read_text(encoding="utf-8")
    카드 = screen[screen.index("const series = ") : screen.index("const renewing")]
    assert "data.mrr_months" in 카드 and "data.cash_months" in 카드
    assert "plan_status" not in 카드 and "activeRows" not in 카드
    # 환산도 화면이 하지 않습니다 — 서버가 계약마다 그 계약의 환율로 두 통화를 다 채워
    # 보냅니다. 화면이 다시 나누면 같은 숫자가 화면마다 달라집니다.
    assert "fx_rate" not in 카드


def test_the_card_counts_gtm_only():
    """Interactive 와 AX 는 각자 매출을 따로 봅니다. 셋을 한 숫자로 더하면 그 카드는 아무
    팀의 숫자도 아닙니다.

    담당부서는 사람이 고칠 수 있는 열이라 그 값이 먼저지만, **비어 있으면 번호대에서
    되짚습니다** — 안 채운 칸 하나가 매출을 조용히 지우면 안 됩니다.
    """
    from src.db.models import Client

    gtm = Client(client_id=1001, company="인바운드 고객")
    outbound = Client(client_id=2001, company="아웃바운드 고객")
    interactive = Client(client_id=3001, company="인터랙티브 고객")
    ax = Client(client_id=4001, company="AX 고객")
    assert [won.department(c) for c in (gtm, outbound, interactive, ax)] == [
        "GTM", "GTM", "Interactive", "AX"
    ]

    # 넘겨받은 고객: 적어 둔 값이 번호대를 이깁니다.
    interactive.department = "GTM"
    assert won.department(interactive) == "GTM"


def test_the_cards_say_which_department_they_counted():
    """거른 숫자에 무엇으로 걸렀는지 안 적으면, 아래 목록을 더한 값과 안 맞을 때 어느 쪽이
    틀린 건지 알 수 없습니다. 이제 담당부서 고르개가 그 값을 정하므로 **고른 값**을 적습니다.

    카드 둘이 **같은 모집단**이어야 합니다: 「고객 12곳에 MRR 3천만원」이 서로 다른 팀의
    숫자면 그 문장은 아무 뜻이 없습니다.
    """
    import pathlib

    screen = pathlib.Path("frontend/src/screens/won/WonCustomers.tsx").read_text(encoding="utf-8")
    for anchor in ('<G name="person" /> 활성 고객', '<G name="trend" /> {metric === "mrr"'):
        label = screen[screen.index(anchor):][:240]
        assert "{deptLabel}" in label, anchor
    # 기본값은 GTM 입니다 — 이 화면을 매일 여는 쪽이고, 「전체」로 두면 세 팀을 합친
    # 숫자로 시작합니다.
    assert 'useState("GTM")' in screen
    # 고르개는 CSV 내보내기 왼쪽, 즉 아래 필터들이 아니라 제목 옆입니다.
    assert screen.index('id="won-dept"') < screen.index("/won-customers/export.csv")


def test_the_row_shows_what_this_customer_added_this_month():
    """목록의 「이번달 매출」 칸 — 위 카드가 더하는 그 값입니다.

    **계약 전부를 훑습니다.** 행에 실리는 계약은 활성 하나뿐이라, 그것만 보면 한 고객의
    다른 계약이 이번 달에 돌고 있어도 안 잡힙니다.
    """
    from datetime import date

    from src.api.routes.ui_api import _won_client

    today = date(2026, 8, 12)
    client = Client(client_id=1001, company="두 계약 고객")
    client.contracts = [
        # 12개월 · 총액 13,200,000 → 매달 1,100,000
        ClientContract(client_id=1001, seq=1, deal_type="MRR", currency="KRW",
                       starts_on="2026-01-01", ends_on="2026-12-31",
                       amount_excl_vat=12_000_000),
        # 첫 결제가 이번 달인 PoC → 전액. 쪼개지 않습니다.
        ClientContract(client_id=1001, seq=2, deal_type="PoC", currency="KRW",
                       starts_on="2026-08-01", ends_on="2026-09-30",
                       amount_excl_vat=1_000_000,
                       payments=[ContractPayment(no=1, total=1, paid_on="2026-08-10",
                                                 amount=1_100_000)]),
    ]
    for contract in client.contracts:
        contract.credit_grants, contract.claims = [], []
        contract.payments = list(contract.payments or [])

    row = _won_client(client, today, full=False)
    assert row["month_revenue"] == {"KRW": Decimal("2200000.0")}   # 1,100,000 + 1,100,000

    # 지난달이 첫 결제인 PoC 는 이번 달에 0 — MRR 한 건만 남습니다.
    client.contracts[1].payments[0].paid_on = "2026-07-10"
    assert _won_client(client, today, full=False)["month_revenue"] == {"KRW": Decimal("1100000.0")}

    # 통화는 안 섞습니다 — 환산은 카드가 오늘 고시가로 한 번만 합니다.
    client.contracts[1].currency = "USD"
    client.contracts[1].amount_incl_vat = 5_000
    client.contracts[1].payments[0].paid_on = "2026-08-10"
    assert _won_client(client, today, full=False)["month_revenue"] == {
        "KRW": Decimal("1100000.0"), "USD": Decimal("5000"),
    }


# --------------------------------------------------------------------------- #
# VAT — 계약서가 총액으로 적히는 원화 계약
# --------------------------------------------------------------------------- #
def test_a_krw_contract_written_as_a_total_keeps_that_total():
    """**계약서에 적힌 금액이 기준입니다.** 원화 계약이 늘 공급가로 적히지는 않습니다.

    총액으로 적힌 계약을 공급가 칸에 넣으면 총액이 10% 부풀고 분당 단가가 10% 낮게
    나오는데, 화면 어디에도 그게 보이지 않습니다 — 그래서 어느 쪽인지를 행에 박아 둡니다.

    같은 금액을 두 기준으로 넣어 비교합니다: 11,000,000 을 총액으로 적으면 총액도 단가
    기준도 그대로 11,000,000 이고, 공급가로 적으면 총액이 12,100,000 이 됩니다.
    """
    total = ClientContract(
        client_id=1, seq=1, currency="KRW", vat_included=True,
        amount_incl_vat=11_000_000, credits=60_000,
    )
    assert won.billing_amount(total) == Decimal("11000000")
    assert won.total_amount(total) == Decimal("11000000")
    assert won.unit_price(total) == Decimal("11000")

    supply = ClientContract(
        client_id=2, seq=1, currency="KRW", vat_included=False,
        amount_excl_vat=11_000_000, credits=60_000,
    )
    assert won.total_amount(supply) == Decimal("12100000.0")
    assert won.unit_price(supply) == Decimal("11000")


def test_a_foreign_contract_never_reads_the_vat_flag():
    """해외 계약에는 부가세가 없어 총액이 곧 대금입니다 — 고를 것이 없습니다.

    통화와 함께 보지 않으면, 원화였다가 USD 로 바꾼 계약에 남은 플래그가 조용히 따라
    붙습니다. `vat_included()` 가 통화까지 보는 이유입니다.
    """
    usd = ClientContract(
        client_id=1, seq=1, currency="USD", vat_included=True,
        amount_incl_vat=20_000, credits=60_000,
    )
    assert won.vat_included(usd) is False
    assert won.total_amount(usd) == Decimal("20000")
    assert won.unit_price(usd) == Decimal("20")


def test_the_mrr_is_always_the_vat_inclusive_total():
    """기준이 무엇이든 예상 MRR 이 더하는 값은 **VAT 포함 총액** 하나입니다.

    같은 총액 22,000,000 짜리 12개월 계약을 두 기준으로 적어도 월간 매출은 같아야 합니다 —
    다르면 그 카드의 숫자가 계약을 어떻게 입력했느냐에 따라 달라집니다.
    """
    written_as_total = ClientContract(
        client_id=1, seq=1, deal_type="MRR", currency="KRW", vat_included=True,
        starts_on="2026-01-01", ends_on="2027-01-01", amount_incl_vat=22_000_000,
    )
    written_as_supply = ClientContract(
        client_id=2, seq=1, deal_type="MRR", currency="KRW", vat_included=False,
        starts_on="2026-01-01", ends_on="2027-01-01", amount_excl_vat=20_000_000,
    )
    assert won.monthly_revenue(written_as_total) == won.monthly_revenue(written_as_supply)


def test_the_form_lets_the_operator_pick_the_supply_basis():
    """분당 단가가 어느 금액에서 나오는지는 사람이 고릅니다.

    계약서가 총액으로 적힌 건과 공급가로 적힌 건이 둘 다 있어서, 고르지 않으면 같은 화면의
    계약마다 단가가 10% 씩 달라집니다.
    """
    import pathlib

    form = pathlib.Path("frontend/src/screens/won/WonContractForm.tsx").read_text(encoding="utf-8")
    assert '<Field label="공급가 (분당단가 기준)">' in form
    assert "VAT 미포함 금액으로" in form
    assert "VAT 포함 금액으로" in form
    # 고를 것이 있는지는 **부가세 해당 여부**가 정합니다 — 통화가 아니라.
    assert "const inclusive = vatApplicable && draft?.vat_included ===" in form


def test_the_form_asks_in_the_order_the_answers_depend_on():
    """부가세 해당 여부 → 통화 → 환율 → 금액 → 공급가.

    앞의 것이 뒤의 것을 정합니다: 해당 여부가 금액 칸을 한 개로 할지 두 개로 할지 정하고,
    통화가 환율을 물어볼지 말지 정합니다. 순서가 뒤집히면 이미 적은 금액이 뒤늦게 바뀐
    해당 여부 때문에 다른 뜻이 됩니다.
    """
    import pathlib

    form = pathlib.Path("frontend/src/screens/won/WonContractForm.tsx").read_text(encoding="utf-8")
    money = form[form.index('<div className="form-sec">금액</div>'):]
    order = [
        money.index('label="VAT 해당 여부"'),
        money.index('label="통화"'),
        money.index('label="환율 (선택)"'),
        money.index('label="총 계약금액 (VAT 포함)"'),
        money.index('label="공급가 (분당단가 기준)"'),
    ]
    assert order == sorted(order), "금액 구역의 칸 순서가 스펙과 다릅니다"
    # 원화는 환산할 것이 없어 환율을 묻지 않습니다.
    assert 'draft.currency !== "KRW" && (' in money



def test_the_supply_price_is_filled_even_when_the_contract_is_written_as_a_total():
    """워크북의 공급가 열은 회계가 합계를 내는 칸입니다 — 비면 그 행만 조용히 빠집니다.

    계약서에 그 숫자가 없더라도 국내 거래의 공급가는 총액에서 정확히 나옵니다. 화면도 같은
    값을 보여 주되 「역산」이라고 적습니다 — 시트와 화면이 다른 값이면 안 됩니다.
    """
    total = ClientContract(
        client_id=1, seq=1, currency="KRW", vat_included=True, amount_incl_vat=11_000_000,
    )
    assert won.supply_amount(total) == Decimal("10000000")

    supply = ClientContract(
        client_id=2, seq=1, currency="KRW", vat_included=False, amount_excl_vat=10_000_000,
    )
    assert won.supply_amount(supply) == Decimal("10000000")

    # 해외 계약에는 공급가가 없습니다 — 총액이 곧 대금입니다.
    usd = ClientContract(client_id=3, seq=1, currency="USD", amount_incl_vat=20_000)
    assert won.supply_amount(usd) is None


def test_the_csv_supply_column_is_the_supply_price_not_the_written_amount(factory):
    """CSV 의 「공급가 (VAT 제외)」 칸은 `supply_amount` 여야 합니다.

    `billing_amount` 는 **계약서에 적힌 금액**이라, VAT 포함으로 적힌 원화 계약에서는 총액을
    돌려줍니다. 그 값을 공급가 칸에 넣으면 과세표준이 10% 부풀고, 바로 옆 총액 칸이 그럴듯해서
    아무도 눈치채지 못합니다. 이 CSV 는 영업 시트에 붙여 넣으라고 있는 것이라 그대로 퍼집니다.
    """
    import csv
    import io
    from unittest.mock import patch

    from src.api.routes import won_customers

    with factory() as session:
        session.add(Client(client_id=1108, company="총액으로 적힌 고객"))
        session.flush()
        session.add(ClientContract(
            client_id=1108, seq=1, deal_type="MRR", currency="KRW", vat_included=True,
            starts_on="2026-01-01", ends_on="2027-01-01",
            amount_incl_vat=11_000_000, credits=60_000,
        ))
        session.commit()

    from fastapi.testclient import TestClient

    from src.api.main import app

    with patch.object(won_customers, "SessionLocal", factory), TestClient(app) as client:
        body = client.get("/won-customers/export.csv").content.decode("utf-8-sig")

    rows = list(csv.reader(io.StringIO(body)))
    header, row = rows[0], rows[1]
    total = row[header.index("총 계약금액 (VAT 포함)")]
    supply = row[header.index("공급가 (VAT 제외)")]
    assert float(total) == 11_000_000
    assert float(supply) == 10_000_000
    # 분당 단가는 **적힌 금액** 기준입니다 — 총액으로 적혔으면 총액에서 나옵니다.
    assert float(row[header.index("분당 단가")]) == 11_000


def test_the_mrr_divisor_is_the_plan_period_not_the_contract_period():
    """계약은 먼저 맺고 실제 사용은 늦게 시작하는 일이 흔합니다(운영자 확인).

    계약 기간으로 나누면 아직 쓰지도 않는 달에 매출이 잡히고 정작 쓰는 달에는 덜 잡힙니다.
    플랜 기간으로 나누면 **월별 합계가 총 계약금액과 정확히 맞습니다** — 그게 이 규칙을
    고르는 이유입니다.
    """
    contract = ClientContract(
        client_id=1, seq=1, deal_type="MRR", currency="KRW", vat_applicable=True,
        vat_included=True, amount_incl_vat=12_000_000,
        starts_on="2026-01-01", ends_on="2026-12-31",          # 계약 12개월
        plan_starts_on="2026-03-01", plan_ends_on="2026-12-31",  # 플랜 10개월
    )
    assert won.plan_months(contract) == 10
    assert won.monthly_revenue(contract) == Decimal("1200000")

    months = [f"2026-{m:02d}" for m in range(1, 13)]
    recognised = {m: won.revenue_in_month(contract, m) for m in months}
    assert sum(recognised.values()) == Decimal("12000000"), "월별 합계가 총액과 맞아야 합니다"
    # 인식은 플랜 시작월부터입니다 — 계약만 맺힌 1·2월은 0.
    assert recognised["2026-01"] == 0 and recognised["2026-02"] == 0
    assert recognised["2026-03"] == Decimal("1200000")


def test_a_terminated_contract_stops_and_settles_in_that_month():
    """중도 해지: 그 달에 `총액 − 예상 환불 − 이미 인식한 MRR` 을 한 번에 잡고 끝냅니다."""
    contract = ClientContract(
        client_id=2, seq=1, deal_type="MRR", currency="KRW", vat_applicable=True,
        vat_included=True, amount_incl_vat=12_000_000, credits=120_000, credits_used=30_000,
        starts_on="2026-01-01", ends_on="2026-12-31",
        plan_starts_on="2026-01-01", plan_ends_on="2026-12-31",
        terminated_on="2026-04-15",
    )
    # 남은 크레딧 90,000 / 120,000 = 0.75 → 환불 900만
    assert won.expected_refund(contract) == Decimal("9000000")
    # 1~3월에 100만씩 인식했으므로 정산 = 1,200 − 900 − 300 = 0
    assert won.termination_adjustment(contract) == Decimal("0")

    got = {m: won.revenue_in_month(contract, m) for m in [f"2026-{i:02d}" for i in range(1, 13)]}
    assert got["2026-03"] == Decimal("1000000")
    assert got["2026-04"] == Decimal("0"), "해지월은 정산액입니다"
    assert got["2026-05"] == Decimal("0") and got["2026-12"] == Decimal("0")
    # 실제로 번 돈(총액 − 환불)과 인식 합계가 같습니다.
    assert sum(got.values()) == Decimal("12000000") - Decimal("9000000")


def test_the_settlement_can_be_negative():
    """이미 인식한 것이 실제로 번 돈보다 많으면 그 달 매출은 마이너스입니다.

    지난달들을 소급해 고치는 대신 이번 달에 한 번에 털어냅니다 — 마감한 달의 숫자가 나중에
    바뀌면 그 달 보고서가 전부 거짓이 됩니다.
    """
    contract = ClientContract(
        client_id=3, seq=1, deal_type="MRR", currency="KRW", vat_applicable=True,
        vat_included=True, amount_incl_vat=12_000_000, credits=120_000, credits_used=6_000,
        starts_on="2026-01-01", ends_on="2026-12-31",
        plan_starts_on="2026-01-01", plan_ends_on="2026-12-31",
        terminated_on="2026-11-20",
    )
    # 거의 안 썼으니 환불이 큽니다: 114,000/120,000 = 0.95 → 1,140만
    assert won.expected_refund(contract) == Decimal("11400000")
    # 10개월치 1,000만을 이미 인식 → 1,200 − 1,140 − 1,000 = −940만
    assert won.termination_adjustment(contract) == Decimal("-9400000")
    assert won.revenue_in_month(contract, "2026-11") == Decimal("-9400000")


def test_without_a_usage_number_we_stop_but_do_not_settle():
    """크레딧 사용량이 비면 환불액을 모릅니다. 모르는 값으로 계산한 숫자를 매출이라고
    적지 않습니다 — 인식만 멈춥니다."""
    contract = ClientContract(
        client_id=4, seq=1, deal_type="MRR", currency="KRW", vat_applicable=True,
        vat_included=True, amount_incl_vat=12_000_000, credits=120_000,
        starts_on="2026-01-01", ends_on="2026-12-31",
        plan_starts_on="2026-01-01", plan_ends_on="2026-12-31",
        terminated_on="2026-04-15",
    )
    assert contract.credits_used is None
    assert won.expected_refund(contract) is None
    assert won.termination_adjustment(contract) is None
    # 해지월까지는 평소대로, 그 뒤로는 0.
    assert won.revenue_in_month(contract, "2026-04") == Decimal("1000000")
    assert won.revenue_in_month(contract, "2026-05") == Decimal("0")


def test_using_more_credits_than_the_contract_refunds_nothing():
    """음수 환불은 추가 청구인데, 그건 이 화면이 정할 일이 아닙니다."""
    contract = ClientContract(
        client_id=5, seq=1, deal_type="MRR", currency="KRW", vat_applicable=True,
        vat_included=True, amount_incl_vat=1_000_000, credits=1_000, credits_used=1_500,
        starts_on="2026-01-01", ends_on="2026-10-31",
        plan_starts_on="2026-01-01", plan_ends_on="2026-10-31",
        terminated_on="2026-05-10",
    )
    assert won.expected_refund(contract) == Decimal("0")


def test_the_plan_ends_at_whichever_comes_first():
    """플랜 만료일과 중도 해지일 중 **빠른 쪽**에서 끝납니다."""
    early = ClientContract(client_id=6, seq=1, plan_starts_on="2026-01-01",
                           plan_ends_on="2026-12-31", terminated_on="2026-06-30")
    assert won.plan_period(early) == ("2026-01-01", "2026-06-30")
    # 해지일이 만료일보다 뒤면 만료일이 이깁니다 — 이미 끝난 계약을 늘리지 않습니다.
    late = ClientContract(client_id=7, seq=1, plan_starts_on="2026-01-01",
                          plan_ends_on="2026-06-30", terminated_on="2026-12-31")
    assert won.plan_period(late) == ("2026-01-01", "2026-06-30")
    # 해지해도 **분모**는 플랜 만료일까지입니다 — 월 요금이 갑자기 오르면 안 됩니다.
    assert won.plan_months(early) == won.plan_months(
        ClientContract(client_id=8, seq=1, plan_starts_on="2026-01-01", plan_ends_on="2026-12-31")
    )


def test_the_series_converts_with_the_contracts_own_rate():
    """환산은 **계약에 박힌 환율**로 합니다. 오늘 고시가로 과거를 다시 환산하면 마감한 달의
    숫자가 오늘 환율에 따라 움직입니다 — 지난달 매출이 이번 달에 바뀝니다."""
    from datetime import date

    from src.api.routes.ui_api import _mrr_cells, _recent_months

    contract = ClientContract(
        client_id=1, seq=1, deal_type="MRR", currency="USD", vat_applicable=False,
        amount_incl_vat=12_000, fx_rate=Decimal("1200"),
        starts_on="2026-01-01", ends_on="2026-12-31",
        plan_starts_on="2026-01-01", plan_ends_on="2026-12-31",
    )
    months = _recent_months(date(2026, 6, 15), 12)
    assert months[-1] == "2026-06" and len(months) == 12

    # 오늘 고시가가 1,400 이어도 이 계약은 1,200 을 씁니다.
    cells = _mrr_cells(contract, months, Decimal("1400"))
    assert cells["2026-06"]["USD"] == Decimal("1000")
    assert cells["2026-06"]["KRW"] == Decimal("1200000"), "계약 환율 1,200 을 씁니다"


def test_a_contract_with_no_rate_falls_back_to_todays():
    """환율 칸이 생기기 전의 계약에는 박힌 값이 없습니다. 그때만 오늘 고시가입니다."""
    from datetime import date

    from src.api.routes.ui_api import _mrr_cells, _recent_months

    옛계약 = ClientContract(
        client_id=2, seq=1, deal_type="MRR", currency="USD", vat_applicable=False,
        amount_incl_vat=12_000,
        starts_on="2026-01-01", ends_on="2026-12-31",
        plan_starts_on="2026-01-01", plan_ends_on="2026-12-31",
    )
    cells = _mrr_cells(옛계약, _recent_months(date(2026, 6, 15), 12), Decimal("1400"))
    assert cells["2026-06"]["KRW"] == Decimal("1400000")


def test_cash_lands_whole_in_the_month_of_each_instalment():
    """월 매출은 **결제 회차가 잡힌 달**에 통째로 얹습니다 — MRR 처럼 기간에 나누지 않습니다.

    같은 계약이 두 지표에서 다르게 보이는 것이 이 카드의 요점입니다: MRR 은 매달 100만씩
    고르게, 월 매출은 결제한 달에 600만이 한 번에.
    """
    from datetime import date

    from src.db.models import ContractPayment
    from src.api.routes.ui_api import _cash_cells, _mrr_cells, _recent_months

    contract = ClientContract(
        client_id=3, seq=1, deal_type="MRR", currency="KRW", vat_applicable=True,
        vat_included=True, amount_incl_vat=12_000_000,
        starts_on="2026-01-01", ends_on="2026-12-31",
        plan_starts_on="2026-01-01", plan_ends_on="2026-12-31",
    )
    contract.payments = [
        ContractPayment(no=1, total=2, paid_on="2026-01-10", amount=Decimal("6000000"), done=True),
        ContractPayment(no=2, total=2, paid_on="2026-07-10", amount=Decimal("6000000"), done=False),
    ]
    months = _recent_months(date(2026, 6, 15), 12)   # 2025-07 ~ 2026-06

    cash = _cash_cells(contract, months, Decimal("1400"))
    assert cash["2026-01"]["KRW"] == Decimal("6000000"), "1회차가 그 달에 통째로"
    assert "2026-02" not in cash, "결제가 없는 달은 0 입니다"
    assert "2026-07" not in cash, "이 창 밖의 회차는 안 셉니다"

    mrr = _mrr_cells(contract, months, Decimal("1400"))
    assert mrr["2026-01"]["KRW"] == Decimal("1000000"), "MRR 은 같은 달에도 한 달치뿐"


def test_the_series_is_bucketed_per_department_and_a_total():
    """「전체」도 서버가 같이 만듭니다 — 화면이 부서별 값을 다시 더하면 그 덧셈이 두 곳에
    생기고, 언젠가 두 숫자가 갈라집니다."""
    from src.api.routes.ui_api import _add_series

    target: dict = {}
    _add_series(target, ("GTM", won.ALL_DEPARTMENTS), ["2026-06"],
                {"2026-06": {"KRW": Decimal("100"), "USD": Decimal("1")}})
    _add_series(target, ("AX", won.ALL_DEPARTMENTS), ["2026-06"],
                {"2026-06": {"KRW": Decimal("300"), "USD": Decimal("3")}})

    assert target["GTM"]["2026-06"]["KRW"] == Decimal("100")
    assert target["AX"]["2026-06"]["KRW"] == Decimal("300")
    assert target[won.ALL_DEPARTMENTS]["2026-06"]["KRW"] == Decimal("400")


def test_the_renewal_list_sits_with_the_other_due_boards():
    """갱신 임박은 크레딧·결제 예정과 같은 성격입니다 — 날짜가 다가와 손이 가야 하는 목록.

    보드 줄은 처음부터 3열이었고 한 칸이 비어 있었습니다(won.css `.board`). KPI 줄에 있던
    카드를 그 칸으로 옮기면 「이번 주에 볼 것」이 한자리에 모입니다(2026-08-18, 운영자 지시).

    **제목이 곧 필터라는 것도 같이 옮겨야 합니다.** 예전 KPI 카드는 누르면 아래 목록이
    갱신 임박만 남았습니다 — 카드를 옮기면서 그 기능이 사라지면 옮긴 것이 아니라 지운
    것입니다.
    """
    import pathlib

    screen = pathlib.Path("frontend/src/screens/won/WonCustomers.tsx").read_text(encoding="utf-8")
    board = screen[screen.index('<div className="board">') : screen.index("{data.pending.length > 0")]
    assert '<Board title="갱신 임박 고객"' in board, "보드 줄 안에 있어야 합니다"
    assert 'setView(view === "갱신임박"' in board, "제목이 곧 필터입니다"
    # KPI 줄에는 더 이상 없습니다.
    kpis = screen[screen.index('<div className="kpi-row">') : screen.index('<div className="board">')]
    assert "갱신 임박" not in kpis
