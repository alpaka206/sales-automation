"""워크북의 수주 고객 탭을 DB 로 채운다 — 시트 → DB 방향은 여기 하나뿐입니다.

평소 방향은 반대입니다(콘솔이 쓰면 ``won_sheets`` 가 시트를 맞춥니다). 이건 그 반대를 딱
한 번 하는 것입니다 — 값이 시트에만 있는 상태에서 첫 한 벌을 채워 넣어야 그 다음부터
평소 방향이 성립합니다.

**자연키로 맞춥니다**(Client ID / +계약 차수 / +회차). 여러 번 돌려도 행이 늘지 않습니다.

**회사 명단 전부를 고객으로 만들지 않습니다.** 그 탭에는 문의만 하고 수주 전인 회사가 더
많습니다. 계약이 있거나 최초 수주일이 적힌 회사만 ``clients`` 가 됩니다 — 금액도 기간도
없는 행이 활성 고객 수와 예상 MRR 을 오염시키면 안 됩니다.

**모르는 것은 비웁니다.** 시트에 없는 칸(고객 담당자·연락처, 공급가)은 NULL 로 둡니다.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from ..common.config import settings
from ..db.models import (
    Client,
    ClientContract,
    Contact,
    ContractCreditGrant,
    ContractPayment,
)
from ..db.session import SessionLocal
from ..integrations import google_sheets as gs

logger = logging.getLogger(__name__)

CLIENTS_TAB = "고객 기본 정보"
CONTRACTS_TAB = "계약 및 결제 정보"
CREDITS_TAB = "크레딧 지급 현황"
PAYMENTS_TAB = "결제 현황"



def text(value: object) -> str | None:
    stripped = str(value or "").strip()
    return stripped or None


def num(value: object) -> Decimal | None:
    raw = str(value or "").replace(",", "").replace("₩", "").replace("$", "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def whole(value: object) -> int | None:
    found = num(value)
    return int(found) if found is not None else None


def cell(row: list, letter: str) -> str:
    index = 0
    for char in letter:
        index = index * 26 + (ord(char) - 64)
    index -= 1
    return str(row[index]).strip() if index < len(row) else ""


def read(sheets, tab: str, last: str) -> list[list[str]]:
    rows = (
        sheets.values()
        .get(spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(), range=f"'{tab}'!A2:{last}1000")
        .execute()
        .get("values")
        or []
    )
    return [r for r in rows if r and str(r[0]).replace(",", "").strip().isdigit()]


def import_from_sheet(write: bool = True) -> dict:
    """워크북의 수주 고객 탭을 DB 로 채운다. 자연키로 맞추므로 몇 번 돌려도 안전하다."""
    sheets = gs._build_service().spreadsheets()

    companies = read(sheets, CLIENTS_TAB, "J")
    contracts = read(sheets, CONTRACTS_TAB, "AM")
    credits = read(sheets, CREDITS_TAB, "J")
    payments = read(sheets, PAYMENTS_TAB, "H")

    # 계약이 있거나 최초 수주일이 적힌 회사만 고객이 된다.
    with_contract = {cell(r, "A") for r in contracts}
    wanted = [
        r for r in companies if cell(r, "A") in with_contract or cell(r, "I")
    ]
    # 계약별 담당 중 마지막 차수의 사람이 그 고객의 현재 담당이다.
    owner: dict[str, tuple[int, str]] = {}
    for r in contracts:
        seq = whole(cell(r, "C")) or 1
        person = cell(r, "AM")
        if person and owner.get(cell(r, "A"), (0, ""))[0] <= seq:
            owner[cell(r, "A")] = (seq, person)

    if not write:
        return {
            "client": len(wanted),
            "contract": len(contracts),
            "credit": len(credits),
            "payment": len(payments),
            "dry_run": 1,
        }

    counts = dict.fromkeys(("client", "contract", "credit", "payment"), 0)
    with SessionLocal() as session:
        linkable = {
            c.sheet_client_id: c.id
            for c in session.scalars(select(Contact).where(Contact.sheet_client_id.isnot(None)))
        }
        for r in wanted:
            client_id = int(cell(r, "A"))
            client = session.get(Client, client_id) or Client(client_id=client_id)
            client.company = cell(r, "C") or client.company or "이름 미확인"
            client.industry = text(cell(r, "E"))
            client.country = text(cell(r, "F"))
            client.department = text(cell(r, "G"))
            client.first_won_on = text(cell(r, "I"))
            # J열(플랜 상태)은 읽지 않습니다 — 계약 기간에서 나오는 값이라 우리가 계산해
            # 시트로 **내보내는** 쪽입니다(won.plan_status → won_sheets). 여기서 읽어 두면
            # 시트에 손으로 적힌 옛 값이 계약 날짜를 이깁니다.
            client.owner = owner.get(cell(r, "A"), (0, None))[1]
            client.contact_id = client.contact_id or linkable.get(client_id)
            session.add(client)
            counts["client"] += 1
        session.flush()

        known = {c.client_id for c in session.scalars(select(Client))}
        by_key: dict[tuple[int, int], ClientContract] = {}
        for r in contracts:
            client_id, seq = int(cell(r, "A")), whole(cell(r, "C")) or 1
            if client_id not in known:
                continue
            contract = session.scalars(
                select(ClientContract).where(
                    ClientContract.client_id == client_id, ClientContract.seq == seq
                )
            ).first() or ClientContract(client_id=client_id, seq=seq)
            contract.ticket_id = text(cell(r, "D"))
            contract.deal_type = cell(r, "E") or "MRR"
            contract.starts_on = text(cell(r, "F"))
            contract.ends_on = text(cell(r, "G"))
            contract.doc_types = [p.strip() for p in cell(r, "I").split("+") if p.strip()] or None
            contract.credits = whole(cell(r, "J"))
            contract.currency = cell(r, "K") or "KRW"
            contract.amount_incl_vat = num(cell(r, "L"))
            contract.amount_excl_vat = num(cell(r, "M"))
            # N(단가 통화)·O(분당 단가)·P(환율)은 읽지 않습니다 — 단가는 금액과 크레딧
            # 에서 나오는 계산값이고, 나머지 둘은 없어진 칸입니다.
            contract.payment_method = text(cell(r, "Q"))
            contract.payment_type = text(cell(r, "R"))
            contract.installments = whole(cell(r, "S"))
            contract.first_payment_on = text(cell(r, "T"))
            contract.billing_email = text(cell(r, "U"))
            contract.note = text(cell(r, "V"))
            contract.renewal_plan = text(cell(r, "W"))
            contract.stop_reason = text(cell(r, "X"))
            contract.memo = text(cell(r, "Y"))
            contract.revenue_from = text(cell(r, "Z"))
            contract.plan = text(cell(r, "AB"))
            contract.plan_name = text(cell(r, "AC"))
            contract.perso_email = text(cell(r, "AD"))
            contract.plan_starts_on = text(cell(r, "AE"))
            contract.plan_ends_on = text(cell(r, "AF"))
            contract.invite_limit = whole(cell(r, "AH"))
            contract.queue_limit = whole(cell(r, "AI"))
            contract.concurrent_jobs = whole(cell(r, "AJ"))
            contract.space_count = whole(cell(r, "AK"))
            contract.space_seq = text(cell(r, "AL"))
            session.add(contract)
            by_key[(client_id, seq)] = contract
            counts["contract"] += 1
        session.flush()

        # 전체 회차는 시트에서 수식이라 값이 없다 — 여기서 세어 넣는다.
        totals: dict[tuple[int, int], int] = {}
        for rows, key in ((credits, "credit"), (payments, "payment")):
            for r in rows:
                totals[(key, int(cell(r, "A")), whole(cell(r, "C")) or 1)] = totals.get(
                    (key, int(cell(r, "A")), whole(cell(r, "C")) or 1), 0
                ) + 1

        for r in credits:
            contract = by_key.get((int(cell(r, "A")), whole(cell(r, "C")) or 1))
            if contract is None:
                continue
            no = whole(cell(r, "D")) or 1
            grant = session.scalars(
                select(ContractCreditGrant).where(
                    ContractCreditGrant.contract_id == contract.id,
                    ContractCreditGrant.no == no,
                )
            ).first() or ContractCreditGrant(contract_id=contract.id, no=no)
            grant.total = totals.get(("credit", int(cell(r, "A")), contract.seq), 1)
            grant.grant_on = text(cell(r, "F"))
            grant.amount = whole(cell(r, "G"))
            grant.granted_by = text(cell(r, "H"))
            grant.done = cell(r, "I") == "지급 완료"
            grant.memo = text(cell(r, "J"))
            session.add(grant)
            counts["credit"] += 1

        for r in payments:
            contract = by_key.get((int(cell(r, "A")), whole(cell(r, "C")) or 1))
            if contract is None:
                continue
            no = whole(cell(r, "D")) or 1
            payment = session.scalars(
                select(ContractPayment).where(
                    ContractPayment.contract_id == contract.id, ContractPayment.no == no
                )
            ).first() or ContractPayment(contract_id=contract.id, no=no)
            payment.total = totals.get(("payment", int(cell(r, "A")), contract.seq), 1)
            payment.paid_on = text(cell(r, "F"))
            payment.amount = num(cell(r, "G"))
            payment.done = cell(r, "H") == "입금 완료"
            session.add(payment)
            counts["payment"] += 1


        session.commit()

    logger.info("시트 → DB: %s", counts)
    return counts

