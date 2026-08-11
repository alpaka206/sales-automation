"""수주 고객 화면이 계산해서 보여주는 값들 — 저장하지 않는 것만 모았습니다.

여기 있는 것은 전부 **입력이 아니라 결과**입니다. 계약 개월수, 다음 지급일, 수금율, 월간
매출, 고객 종류… 저장해 두면 원본이 바뀌었는데 파생값은 안 바뀐 상태가 생기고, 화면에는
그게 안 보입니다. 한 번 더 계산하는 편이 항상 쌉니다 — 행이 수백 개짜리 장부입니다.

날짜는 ``YYYY-MM-DD`` 문자열로 다룹니다. 계약 시작일·지급 예정일은 시각이 없는 날짜이고,
timestamp 로 저장하면 시간대 때문에 하루가 밀립니다(운영자는 KST, 서버는 UTC).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

# 1분 = 60크레딧. 고정입니다.
CREDITS_PER_MINUTE = 60

# Client ID 번호대가 곧 고객 종류입니다. 종류를 따로 저장하지 않는 이유가 이 표입니다 —
# 두 군데 두면 번호대와 종류가 서로 다른 행이 반드시 생깁니다.
CLIENT_ID_BANDS: tuple[tuple[int, str], ...] = (
    (9000, "2025 Inbound"),
    (4000, "AX"),
    (3000, "Interactive"),
    (2000, "GTM Outbound"),
    (1000, "GTM Inbound"),
)
# 신규 발급이 가능한 종류. 9000번대는 레거시라 새로 만들지 않습니다.
ALLOCATABLE_BANDS: dict[str, int] = {
    "GTM Inbound": 1000,
    "GTM Outbound": 2000,
    "Interactive": 3000,
    "AX": 4000,
}
DEPARTMENT_BY_TYPE: dict[str, str] = {
    "GTM Inbound": "GTM",
    "GTM Outbound": "GTM",
    "Interactive": "Interactive",
    "AX": "AX",
    "2025 Inbound": "GTM",
}

PLAN_STATUSES = ("사용중", "세팅중", "사용 중단")
ACTIVE_PLAN_STATUSES = ("사용중", "세팅중")
# 목록 정렬: 손이 가야 하는 것이 위로. 세팅중 → 사용중 → 사용 중단.
PLAN_STATUS_ORDER = {"세팅중": 0, "사용중": 1, "사용 중단": 2}

DEAL_TYPES = ("MRR", "PoC")
PLANS = ("Business Tier 1", "Business Tier 2", "Business Tier 3", "Enterprise")
DOC_TYPES = (
    "해당 없음",
    "직접 계약 / DocuSign",
    "결제 시 약관 및 협의 내용 동의",
    "세금계산서 발행",
)
RENEWAL_PLANS = ("갱신 예정", "협의 중", "미정", "본계약 검토 중", "갱신 안함", "갱신 완료")
CLAIM_PROGRESS = ("접수", "조치 진행 중", "조치 완료")
PAYMENT_METHODS = ("Stripe", "포트원", "계좌이체")
PAYMENT_TYPES = ("일시불", "할부")
CURRENCIES = ("KRW", "USD")
INDUSTRIES = (
    "크리에이터(개인)", "교육", "MCN", "의료", "종교", "기업", "대행사", "확인 안 됨",
    "제작사/엔터사", "스포츠", "뷰티", "공공기관", "출판", "제조", "보안",
)
# HubSpot 파이프라인의 Won type → 수주 유형. **자동으로 채우지 않고 기본값만 제안**합니다:
# Contract 와 Renewal 이 둘 다 MRR 이라, 되묻지 않으면 PoC 였던 건이 조용히 MRR 로 굳습니다.
WON_TYPE_HINT = {"Contract": "MRR", "Renewal": "MRR", "PoC": "PoC"}


def client_type(client_id: int | None) -> str:
    """Client ID 번호대에서 읽는 고객 종류."""
    if not isinstance(client_id, int):
        return "—"
    for floor, label in CLIENT_ID_BANDS:
        if client_id >= floor:
            return label
    return "—"


def parse_date(value: str | None) -> date | None:
    """``YYYY-MM-DD`` 만 받습니다. 형식이 틀리면 None — 화면이 '—' 로 그립니다."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def months_between(start: str | None, end: str | None) -> int:
    """계약 개월수 = (종료일 − 시작일) ÷ 30.44 반올림, 최소 1.

    달력 개월이 아니라 일수 기준입니다. 3월 1일 ~ 2월 28일이 11개월로 나오면 월간 매출이
    한 달치 부풀어서, 운영자가 시트에서 쓰던 계산과 어긋납니다.
    """
    a, b = parse_date(start), parse_date(end)
    if not a or not b:
        return 1
    return max(1, round((b - a).days / 30.4375))


def contract_state(contract, today: date | None = None) -> str:
    """진행 중 / 세팅중 / 종료 — 오늘과 계약기간만 비교합니다."""
    today = today or date.today()
    start, end = parse_date(contract.starts_on), parse_date(contract.ends_on)
    if start and start > today:
        return "세팅중"
    if end and end < today:
        return "종료"
    return "진행 중"


def plan_status(client, today: date | None = None) -> str:
    """플랜 상태 — **계약 기간이 정합니다.** 저장하지 않습니다.

    오늘이 어느 계약 기간 안에 들면 사용중, 아직 시작하지 않았거나 날짜가 덜 적힌 계약이
    있으면 세팅중, 있는 계약이 전부 지났으면 사용 중단입니다. 계약이 아직 하나도 없는
    고객(수주 전환만 된 상태)도 세팅중입니다 — 앞으로 채울 것이 있다는 뜻이므로.

    저장하지 않는 이유는 고객 종류를 저장하지 않는 이유와 같습니다: 날짜에서 나오는 값을
    따로 들고 있으면 반드시 어긋나고, 어긋난 뒤에는 어느 쪽이 맞는지 아무도 모릅니다.
    계약이 끝나도 누가 손으로 바꿔 주기 전까지 「사용중」으로 남아 있던 것이 그 증상입니다.

    ``contract_state`` 를 그대로 쓰지 않는 이유: 그쪽은 날짜가 없으면 「진행 중」으로 읽습니다
    (계약 하나를 놓고 보는 화면이라 그게 맞습니다). 여기서 날짜가 덜 적힌 계약은 아직 쓰는
    중이라는 뜻이라 세팅중이어야 합니다.
    """
    today = today or date.today()
    contracts = list(client.contracts or ())
    if not contracts:
        return "세팅중"
    pending = False
    for contract in contracts:
        start, end = parse_date(contract.starts_on), parse_date(contract.ends_on)
        if end and end < today:
            continue                       # 지난 계약
        if start and start <= today and end:
            return "사용중"                 # 오늘이 기간 안
        pending = True                     # 시작 전이거나 날짜가 덜 적힌 계약
    return "세팅중" if pending else "사용 중단"


def active_contract(client, today: date | None = None):
    """화면이 기본으로 여는 계약 — 오늘이 기간에 든 것, 없으면 가장 최근 차수."""
    today = today or date.today()
    for contract in client.contracts:
        start, end = parse_date(contract.starts_on), parse_date(contract.ends_on)
        if start and end and start <= today <= end:
            return contract
    return max(client.contracts, key=lambda c: c.seq, default=None)


def upcoming_contracts(client, today: date | None = None) -> list:
    """아직 시작 전인 계약 = 세팅중 계약. 1차가 도는 중에 2차를 미리 등록한 경우입니다."""
    today = today or date.today()
    return [c for c in client.contracts if (parse_date(c.starts_on) or date.max) > today]


def next_credit_grant(contract):
    """미지급 회차 중 가장 빠른 날짜 = 다음 지급일."""
    if contract is None:
        return None
    pending = [g for g in contract.credit_grants if not g.done]
    return min(pending, key=lambda g: (g.grant_on or "9999", g.no), default=None)


def next_payment(contract):
    """미입금 회차 중 가장 빠른 날짜 = 다음 결제일."""
    if contract is None:
        return None
    pending = [p for p in contract.payments if not p.done]
    return min(pending, key=lambda p: (p.paid_on or "9999", p.no), default=None)


def open_claims(client) -> list:
    """조치 완료가 아닌 것 전부. '접수'와 '조치 진행 중'은 둘 다 아직 안 끝난 것입니다."""
    return [
        claim
        for contract in client.contracts
        for claim in contract.claims
        if claim.progress != "조치 완료"
    ]


def _decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


# 원화 계약의 부가세. 총액 = 공급가 + 10% 이고, 그래서 원화 계약은 총액을 받지 않습니다.
VAT_RATE = Decimal("0.1")


def is_krw(contract) -> bool:
    return (getattr(contract, "currency", None) or "KRW").upper() == "KRW"


def billing_amount(contract) -> Decimal | None:
    """분당 단가가 기준으로 삼는 금액.

    원화 계약은 **공급가(VAT 제외)**, 그 외 통화는 **총액**입니다. 국내 계약서는 공급가로
    적히고 부가세가 따로 붙지만, 해외 계약에는 그 부가세가 없어 총액이 곧 대금입니다.
    받는 칸이 통화마다 하나뿐인 이유가 이것입니다 — 둘 다 받으면 어느 쪽이 기준인지가
    계약마다 달라집니다.

    **총액만 있는 옛 원화 계약은 거기서 공급가를 되짚습니다.** 공급가를 받기로 하기 전의
    행들은 총액만 채워져 있어서, 그대로 두면 화면의 공급가 칸이 비고 — 필수 칸이라 —
    그 계약은 플랜 하나 고치는 것조차 저장이 막혔습니다. 총액 = 공급가 + 10% 의 정확한
    역이라 값을 지어내는 것이 아니고, 다음 저장 때 공급가로 자리를 옮겨 앉습니다.
    """
    if not is_krw(contract):
        return _decimal(contract.amount_incl_vat)
    supply = _decimal(contract.amount_excl_vat)
    if supply is not None:
        return supply
    total = _decimal(contract.amount_incl_vat)
    return total / (1 + VAT_RATE) if total else None


def total_amount(contract) -> Decimal | None:
    """VAT 포함 총액. 원화 계약은 공급가에 10% 를 더해 **계산합니다**(입력 칸이 없습니다).

    총액만 있는 옛 계약도 같은 답이 나옵니다: `billing_amount` 가 총액에서 공급가를
    되짚고, 여기서 다시 10% 를 더하면 원래 총액입니다.
    """
    if not is_krw(contract):
        return _decimal(contract.amount_incl_vat)
    supply = billing_amount(contract)
    return supply * (1 + VAT_RATE) if supply else None


def unit_price(contract) -> Decimal | None:
    """분당 단가 = 기준 금액 ÷ (계약 크레딧 ÷ 60). **계산값입니다 — 저장하지 않습니다.**

    방향이 뒤집혔습니다. 예전에는 단가를 받아 크레딧을 계산했는데(크레딧 = 공급가 ÷ 단가
    × 60), 계약서에 적히는 것은 금액과 크레딧이고 단가는 그 둘에서 나오는 값입니다. 단가를
    받으면 반올림한 단가로 계산한 크레딧이 계약서의 크레딧과 어긋납니다.

    운영자 시트의 실제 계약으로 검산하면 같은 숫자가 나옵니다:
    1,566,000원 ÷ (64,800 ÷ 60) = 1,450원/분.

    소수점은 남깁니다 — 20,000,000 ÷ (456,120 ÷ 60) 처럼 딱 떨어지지 않는 계약이 흔하고,
    반올림한 단가는 되짚어 곱했을 때 금액이 안 맞습니다.
    """
    amount = billing_amount(contract)
    credits = contract.credits
    if not amount or not credits or credits <= 0:
        return None
    return amount / (Decimal(credits) / CREDITS_PER_MINUTE)


def monthly_revenue(contract) -> Decimal:
    """월간 매출 — MRR 은 VAT 포함 총액 ÷ 개월수, PoC 는 0 (결제월에 전액 인식)."""
    if contract is None or contract.deal_type != "MRR":
        return Decimal(0)
    amount = total_amount(contract)
    if not amount:
        return Decimal(0)
    return amount / months_between(contract.starts_on, contract.ends_on)


def revenue_start_month(contract) -> str | None:
    """매출을 인식하기 시작하는 달. 지정이 없으면 계약 시작월."""
    if contract is None:
        return None
    if contract.revenue_from:
        return contract.revenue_from
    start = parse_date(contract.starts_on)
    return f"{start.year}-{start.month:02d}" if start else None


def collected(contract) -> Decimal:
    """입금 완료된 금액 합계. 수금율은 **항상 계약 통화 기준**입니다 — 환율 환산은
    대시보드의 예상 MRR 에서만 씁니다."""
    if contract is None:
        return Decimal(0)
    return sum((_decimal(p.amount) or Decimal(0) for p in contract.payments if p.done), Decimal(0))


def granted_credits(contract) -> int:
    """누적 지급 크레딧. 계약 크레딧을 넘을 수 있습니다 — 테스트·보상 지급이 있습니다."""
    if contract is None:
        return 0
    return sum(g.amount or 0 for g in contract.credit_grants if g.done)


def previous_business_day(value: date) -> date:
    """주말이면 직전 금요일. 환율은 영업일에만 고시됩니다.

    공휴일은 보지 않습니다 — 달력을 들고 있어야 하고, 그날 고시가 없으면 조회가 빈 값을
    돌려주므로 운영자가 직접 넣게 됩니다. 주말만으로 대부분이 걸립니다.
    """
    while value.weekday() >= 5:  # 5=토, 6=일
        value -= timedelta(days=1)
    return value
