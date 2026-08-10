"""수주 고객을 워크북의 입력 시트로 내보냅니다 — 콘솔에서 바뀌면 시트도 같이 바뀌도록.

운영자가 준 엑셀 템플릿(`scripts/build_won_sheets.py` 가 워크북에 만든 다섯 개 탭)은 사람이
손으로 채우는 것이었습니다. 같은 값이 콘솔에도 있는데 두 곳에 각각 적으면 반드시 갈라지고,
어느 쪽이 맞는지는 아무도 모릅니다. 그래서 콘솔이 쓴 것은 콘솔이 시트에 넣습니다.

**행은 자연키로 찾습니다** — Client ID, 계약 단위 탭은 + 계약 차수, 회차 탭은 + 회차. 셋 다
사람이 고치지 않는 값이라(차수와 회차는 매기고 나면 그대로입니다) 수정은 제자리 덮어쓰기가
됩니다. 한동안 「동기화 키」 열을 따로 뒀는데, 자연키가 이미 안정적이어서 하는 일이 없었습니다.

**콘솔에 있는 고객의 행은 콘솔 것입니다.** 그 Client ID 의 행 중 콘솔이 들고 오지 않은 것은
지워진 항목(클레임 등)이므로 비웁니다. 콘솔에 없는 Client ID 의 행은 손으로 쓴 것이라
건드리지 않고, 나중에 그 고객이 콘솔에 생기면 그 행을 이어받습니다 — 안 그러면 운영자가
먼저 채워 둔 서울대학교가 두 줄이 됩니다.

**수식 칸에는 쓰지 않습니다**(고객사 · 계약 라벨 · 계약 개월수 · 월간 매출 · 잔여일수 ·
고객 종류). 값으로 덮으면 시트가 스스로 계산하던 것이 그 행에서만 멈추고, 화면에는 그게
안 보입니다. 고객 기본 정보의 Website URL 도 같은 이유로 건드리지 않습니다 — 콘솔에 없는
칸이라 시트가 원본입니다.

글자는 RAW, 숫자·날짜는 USER_ENTERED 로 나눠 씁니다. 한 벌로 보내면 둘 중 하나가 깨집니다:
RAW 로 보낸 날짜는 글자라서 `종료일 − 시작일` 이 안 되고, USER_ENTERED 로 보낸 전화번호
``+82 10-…`` 는 수식으로 해석돼 ``#ERROR!`` 가 됩니다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..common import won
from ..common.config import settings
from ..common.safe_mode import ExternalWriteBlocked, guard_external_write
from ..db.models import Client, ClientContract
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

# 시트의 물리적 크기(scripts/build_won_sheets.py 의 GRID_ROWS). 수식은 ARRAYFORMULA 라
# 아래로 저절로 자라므로, 이 값은 "행이 더 안 들어가는 지점" 일 뿐입니다.
MAX_ROW = 1000


def _col(letter: str) -> int:
    index = 0
    for char in letter:
        index = index * 26 + (ord(char) - 64)
    return index - 1


@dataclass(frozen=True)
class _Tab:
    title: str
    natural_cols: tuple[str, ...]
    # 콘솔이 쓰는 열 전부. 행을 비울 때도 이 목록만 지웁니다 — 수식 칸은 그대로 둡니다.
    owned: tuple[str, ...]


@dataclass
class _Row:
    natural: tuple[str, ...]
    entered: dict[str, object] = field(default_factory=dict)  # 숫자·날짜
    raw: dict[str, object] = field(default_factory=dict)  # 글자


# B(고객 종류)·G(담당부서)는 번호대에서 나오는 수식, D(Website URL)와 H(최초 연락일)는
# 시트가 원본이라 콘솔이 안 씁니다. 사람 이름·연락처는 이 탭에 아예 없습니다.
CLIENTS = _Tab("고객 기본 정보", ("A",), tuple("ACEFIJ"))
# Perso 계정·플랜은 계약과 1:1 이라 같은 행(AB~AL)입니다. 탭을 나누면 Client ID·고객사·
# 계약 차수 세 열을 다시 적을 뿐입니다 — db/models.py 가 같은 말을 합니다.
CONTRACTS = _Tab(
    "계약 및 결제 정보", ("A", "C"),
    ("A", "C", "D", "E", "F", "G", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S",
     "T", "U", "V", "W", "X", "Y", "Z",
     "AB", "AC", "AD", "AE", "AF", "AH", "AI", "AJ", "AK", "AL"),
    # AM(담당)은 콘솔에 없는 칸이라 시트가 원본입니다 — 콘솔이 안 건드립니다.
)
# E(전체 회차)는 그 계약의 행 수라 시트가 셉니다 — 회차를 더할 때마다 앞선 행을
# 전부 고쳐야 하는 값이었고, 실제로 어긋나 있었습니다.
CREDITS = _Tab("크레딧 지급 현황", ("A", "C", "D"), tuple("ACDFGHIJ"))
PAYMENTS = _Tab("결제 현황", ("A", "C", "D"), tuple("ACDFGH"))
CLAIMS = _Tab("클레임 · 히스토리", ("A", "C", "D", "E"), tuple("ACDEFGH"))
# 소통 히스토리 탭은 콘솔이 쓰지 않습니다. 내보낼 수 있는 것은 Contact 가 있는 고객,
# 즉 인바운드뿐인데 그 사람들의 타임라인은 콘솔 화면에 이미 있습니다. 정작 그 탭이
# 필요한 쪽(LG전자·외교부처럼 Contact 가 없는 고객)은 내보낼 것이 아예 없습니다.
# 그래서 그 탭은 손으로 적는 자리로 남깁니다.
TABS = (CLIENTS, CONTRACTS, CREDITS, PAYMENTS, CLAIMS)

def _date(value: object) -> str:
    """``YYYY-MM-DD`` 만 통과시킵니다. 시트에 진짜 날짜로 들어가야 뺄셈이 됩니다."""
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    parsed = won.parse_date(value if isinstance(value, str) else None)
    return parsed.isoformat() if parsed else ""


def _num(value: object) -> object:
    """Decimal 은 JSON 이 못 싣습니다. 정수로 떨어지면 정수로 — 금액에 .0 이 붙지 않게."""
    if value is None or value == "":
        return ""
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _natural(*values: object) -> tuple[str, ...]:
    return tuple(str(v if v is not None else "").strip() for v in values)


def _client_row(client: Client) -> _Row:
    return _Row(
        natural=_natural(client.client_id),
        entered={"A": client.client_id, "I": _date(client.first_won_on)},
        raw={
            "C": _text(client.company),
            "E": _text(client.industry),
            "F": _text(client.country),
            "J": _text(client.plan_status),
        },
    )


def _contract_row(contract: ClientContract) -> _Row:
    return _Row(
        natural=_natural(contract.client_id, contract.seq),
        entered={
            "A": contract.client_id,
            "C": contract.seq,
            "F": _date(contract.starts_on),
            "G": _date(contract.ends_on),
            "J": _num(contract.credits),
            "L": _num(contract.amount_incl_vat),
            "M": _num(contract.amount_excl_vat),
            "O": _num(contract.unit_price),
            "P": _num(contract.unit_fx_rate),
            "S": _num(contract.installments),
            "T": _date(contract.first_payment_on),
            "AE": _date(contract.plan_starts_on),
            "AF": _date(contract.plan_ends_on),
            "AH": _num(contract.invite_limit),
            "AI": _num(contract.queue_limit),
            "AJ": _num(contract.concurrent_jobs),
            "AK": _num(contract.space_count),
        },
        raw={
            "D": _text(contract.ticket_id),
            "E": _text(contract.deal_type),
            "I": " + ".join(contract.doc_types or []),
            "K": _text(contract.currency),
            "N": _text(contract.unit_currency),
            "Q": _text(contract.payment_method),
            "R": _text(contract.payment_type),
            "U": _text(contract.billing_email),
            "V": _text(contract.note),
            "W": _text(contract.renewal_plan),
            "X": _text(contract.stop_reason),
            "Y": _text(contract.memo),
            "Z": _text(contract.revenue_from),
            "AB": _text(contract.plan),
            "AC": _text(contract.plan_name),
            "AD": _text(contract.perso_email),
            "AL": _text(contract.space_seq),
        },
    )


def _credit_row(contract: ClientContract, grant) -> _Row:
    return _Row(
        natural=_natural(contract.client_id, contract.seq, grant.no),
        entered={
            "A": contract.client_id,
            "C": contract.seq,
            "D": grant.no,
            "F": _date(grant.grant_on),
            "G": _num(grant.amount),
        },
        raw={
            "H": _text(grant.granted_by),
            "I": "지급 완료" if grant.done else "지급 예정",
            "J": _text(grant.memo),
        },
    )


def _payment_row(contract: ClientContract, payment) -> _Row:
    return _Row(
        natural=_natural(contract.client_id, contract.seq, payment.no),
        entered={
            "A": contract.client_id,
            "C": contract.seq,
            "D": payment.no,
            "F": _date(payment.paid_on),
            "G": _num(payment.amount),
        },
        raw={"H": "입금 완료" if payment.done else "입금 전"},
    )


def _claim_row(contract: ClientContract, claim) -> _Row:
    return _Row(
        natural=_natural(
            contract.client_id, contract.seq, claim.kind, _date(claim.happened_on)
        ),
        entered={
            "A": contract.client_id,
            "C": contract.seq,
            "E": _date(claim.happened_on),
            "H": _date(claim.action_on),
        },
        raw={
            "D": _text(claim.kind),
            "F": _text(claim.compensation),
            "G": _text(claim.progress),
        },
    )


def collect_rows() -> tuple[dict[str, list[_Row]], set[str]]:
    """DB 한 번 읽어 탭별 행 목록과 콘솔이 아는 Client ID 로. 이 함수만 DB 를 압니다."""
    rows: dict[str, list[_Row]] = {tab.title: [] for tab in TABS}
    with SessionLocal() as session:
        clients = session.scalars(
            select(Client)
            .options(
                selectinload(Client.contracts).selectinload(ClientContract.credit_grants),
                selectinload(Client.contracts).selectinload(ClientContract.payments),
                selectinload(Client.contracts).selectinload(ClientContract.claims),
            )
            .order_by(Client.client_id)
        ).all()
        for client in clients:
            rows[CLIENTS.title].append(_client_row(client))
            for contract in client.contracts:
                rows[CONTRACTS.title].append(_contract_row(contract))
                for grant in sorted(contract.credit_grants, key=lambda g: (g.no, g.id)):
                    rows[CREDITS.title].append(_credit_row(contract, grant))
                for payment in sorted(contract.payments, key=lambda p: (p.no, p.id)):
                    rows[PAYMENTS.title].append(_payment_row(contract, payment))
                for claim in sorted(contract.claims, key=lambda c: c.id):
                    rows[CLAIMS.title].append(_claim_row(contract, claim))
        managed = {str(c.client_id) for c in clients}
    return rows, managed


def _runs(tab: str, row: int, cells: dict[str, object]) -> list[dict]:
    """붙어 있는 열끼리 한 범위로 묶습니다 — 칸마다 한 범위면 요청이 열 배가 됩니다."""
    letters = sorted(cells, key=_col)
    groups: list[list[str]] = []
    for letter in letters:
        if groups and _col(letter) == _col(groups[-1][-1]) + 1:
            groups[-1].append(letter)
        else:
            groups.append([letter])
    return [
        {
            "range": f"'{tab}'!{group[0]}{row}:{group[-1]}{row}",
            "values": [[cells[letter] for letter in group]],
        }
        for group in groups
    ]


@dataclass
class _Plan:
    entered: list[dict] = field(default_factory=list)
    raw: list[dict] = field(default_factory=list)
    clears: list[str] = field(default_factory=list)
    dropped: int = 0


def plan_tab(
    tab: _Tab, columns: dict[str, list[str]], rows: list[_Row], managed: set[str]
) -> _Plan:
    """시트에 이미 있는 것과 콘솔의 ``rows`` 를 맞춰 쓸 곳을 정합니다.

    ``columns`` 는 자연키 열만 2행부터 읽어 온 것입니다 — 행 단위로 읽지 않는 이유가 있습니다:
    파생 열은 ARRAYFORMULA 라 시트 끝까지 빈 문자열이 차 있어서, 행으로 읽으면 마지막 행이
    1000행이 되고 빈 행을 하나도 못 찾습니다.

    ``managed`` 는 콘솔이 아는 Client ID(문자열)입니다. 그 고객의 행 중 콘솔이 들고 오지
    않은 것은 지워진 항목이라 비웁니다. 모르는 Client ID 의 행은 손으로 쓴 것이라 그대로
    둡니다 — 그 고객이 나중에 콘솔에 생기면 그때 그 행을 이어받습니다.

    순수 함수입니다 — 여기가 틀리면 남의 행을 덮어쓰므로, 테스트가 붙는 곳도 여기입니다.
    """
    plan = _Plan()
    by_natural: dict[tuple[str, ...], int] = {}
    stale: dict[int, tuple[str, ...]] = {}
    free: list[int] = []
    height = max((len(values) for values in columns.values()), default=0)

    def cell(letter: str, offset: int) -> str:
        values = columns.get(letter) or []
        return str(values[offset]).strip() if offset < len(values) else ""

    for offset in range(min(height, MAX_ROW - 1)):
        number = offset + 2
        natural = tuple(cell(letter, offset) for letter in tab.natural_cols)
        if not any(natural):
            free.append(number)
            continue
        by_natural.setdefault(natural, number)
        if natural[0] in managed:
            stale[number] = natural
    free.extend(range(height + 2, MAX_ROW + 1))

    for row in rows:
        number = by_natural.pop(row.natural, None)
        if number is None:
            if not free:
                plan.dropped += 1
                continue
            number = free.pop(0)
        stale.pop(number, None)
        plan.entered += _runs(tab.title, number, row.entered)
        plan.raw += _runs(tab.title, number, row.raw)

    # 콘솔이 아는 고객인데 콘솔이 안 들고 온 행 = 지워진 것. 수식 칸은 두고 콘솔이 쓰던
    # 칸만 비웁니다.
    for number in sorted(stale):
        plan.clears += [
            entry["range"] for entry in _runs(tab.title, number, {c: "" for c in tab.owned})
        ]
    return plan


def sync_won_sheets() -> dict[str, int]:
    """수주 고객 전체를 시트에 맞춥니다. 호출당 API 는 읽기 1 + 쓰기 2~3회입니다."""
    from ..integrations.google_sheets import _build_service, is_configured

    if not is_configured():
        return {}
    guard_external_write("sheets:won_customers")

    rows, managed = collect_rows()
    service = _build_service()
    spreadsheet_id = settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip()
    # 자연키 열만 한 열씩 읽습니다. 한 범위로 읽으면 ARRAYFORMULA 가 채운 빈 문자열까지
    # 딸려 와 마지막 행이 시트 끝이 되고, 새 행을 놓을 자리를 못 찾습니다.
    wanted = [(tab, letter) for tab in TABS for letter in tab.natural_cols]
    fetched = (
        service.spreadsheets()
        .values()
        .batchGet(
            spreadsheetId=spreadsheet_id,
            ranges=[f"'{tab.title}'!{letter}2:{letter}{MAX_ROW}" for tab, letter in wanted],
        )
        .execute()
        .get("valueRanges")
        or []
    )
    columns: dict[str, dict[str, list[str]]] = {tab.title: {} for tab in TABS}
    for (tab, letter), value_range in zip(wanted, fetched):
        columns[tab.title][letter] = [
            (line[0] if line else "") for line in (value_range.get("values") or [])
        ]

    entered: list[dict] = []
    raw: list[dict] = []
    clears: list[str] = []
    written = 0
    for tab in TABS:
        plan = plan_tab(tab, columns[tab.title], rows[tab.title], managed)
        entered += plan.entered
        raw += plan.raw
        clears += plan.clears
        written += len(rows[tab.title]) - plan.dropped
        if plan.dropped:
            logger.warning(
                "'%s' 탭에 빈 행이 없어 %d행을 넣지 못했습니다 — %d행까지만 수식이 깔려 "
                "있습니다. scripts/build_won_sheets.py 의 ROWS 를 올리세요.",
                tab.title,
                plan.dropped,
                MAX_ROW,
            )

    values = service.spreadsheets().values()
    if entered:
        values.batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": entered},
        ).execute()
    if raw:
        values.batchUpdate(
            spreadsheetId=spreadsheet_id, body={"valueInputOption": "RAW", "data": raw}
        ).execute()
    if clears:
        values.batchClear(spreadsheetId=spreadsheet_id, body={"ranges": clears}).execute()
    return {"rows": written, "cleared": len(clears)}


# --------------------------------------------------------------------------- #
# 콘솔에서 뭔가 저장되면 부르는 쪽
# --------------------------------------------------------------------------- #
# 한 번에 하나만 돕니다. 두 번이 겹치면 둘 다 "이 키는 아직 시트에 없다" 를 보고 같은 행을
# 두 번 만듭니다. 도는 중에 또 저장되면 끝나고 한 번 더 돕니다 — 마지막 저장이 반드시
# 시트에 반영되도록.
# ponytail: 프로세스 안에서만 겹침을 막습니다. 워커를 여럿 띄우면 잠금이 따로 필요합니다.
_running = False
_again = False


def schedule_sync() -> None:
    """요청을 막지 않고 시트를 맞춥니다. 실패해도 콘솔 저장은 이미 끝났습니다."""
    global _running, _again

    if _running:
        _again = True
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # 이벤트 루프 밖(스크립트·테스트) — 부를 일이 없습니다.
        return
    _running = True
    loop.create_task(_sync_until_quiet())


async def _sync_until_quiet() -> None:
    global _running, _again

    try:
        while True:
            _again = False
            try:
                await asyncio.to_thread(sync_won_sheets)
            except ExternalWriteBlocked:
                return  # 안전 모드. 로그는 guard 가 이미 남겼습니다.
            except Exception:
                logger.warning("수주 고객 시트 동기화 실패 (콘솔 저장은 끝났습니다).", exc_info=True)
                return
            if not _again:
                return
    finally:
        _running = False
