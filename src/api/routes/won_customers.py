"""수주 고객 — 쓰기 라우트.

읽기는 ``ui_api`` 에 있습니다. 이 파일은 화면이 값을 바꿀 때 부르는 곳이고, 여기 있는 규칙은
전부 "저장하기 전에 한 번 더 계산한다" 입니다:

- 크레딧은 입력받지 않고 **공급가 ÷ 분당 단가 × 60** 으로 계산합니다. 통화가 다르면 그때 쓴
  환율을 계약 행에 같이 박습니다 — 오늘 환율로 다시 계산하면 작년 계약의 크레딧이 바뀝니다.
- 결제 회차를 입금 완료로 바꿀 때 **그 날짜의 환율**을 채웁니다. 조회에 실패하면 비워 둡니다;
  운영자가 직접 넣을 수 있고, 조회 실패가 저장을 막으면 안 됩니다.
- 계약 차수는 받지 않고 그 고객의 마지막 차수 + 1 입니다.

계약 삭제는 없습니다. 지워야 할 계약은 실수로 만든 것뿐인데, 그건 값을 고치면 되고 — 지우면
거기 딸린 결제·크레딧 기록이 같이 사라집니다.
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request

from ...agents.client_ids import next_client_id
from ...common.won import (
    ALLOCATABLE_BANDS,
    DEPARTMENT_BY_TYPE,
    contract_credits,
)
from ...db.models import (
    Client,
    ClientContract,
    ContractClaim,
    ContractCreditGrant,
    ContractPayment,
    PendingWon,
)
from ...db.session import SessionLocal
from ..auth import actor_name

logger = logging.getLogger(__name__)
router = APIRouter(tags=["web"])


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _number(value: str | None):
    """빈 칸은 None. ``0`` 은 값입니다 — falsy 로 지우면 무료 계약을 저장할 수 없습니다."""
    raw = (value or "").replace(",", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _get_contract(session, contract_id: int) -> ClientContract:
    contract = session.get(ClientContract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="계약을 찾을 수 없습니다")
    return contract


def _apply_credits(contract: ClientContract) -> None:
    """계약 크레딧을 다시 계산해 넣습니다. 공급가·단가·환율이 바뀔 때마다 부릅니다."""
    contract.credits = contract_credits(
        contract.amount_excl_vat,
        contract.unit_price,
        contract.currency,
        contract.unit_currency,
        contract.unit_fx_rate,
    )


# --------------------------------------------------------------------------- #
# 고객
# --------------------------------------------------------------------------- #
@router.post("/won-customers")
async def create_client(
    request: Request,
    customer_type: str = Form(...),
    company: str = Form(...),
    industry: str = Form(""),
    country: str = Form(""),
    contact_name: str = Form(""),
    contact_info: str = Form(""),
    first_won_on: str = Form(""),
    owner: str = Form(""),
    client_id: str = Form(""),
):
    """새 고객. Client ID 는 **고객 종류가 정합니다** — 번호대가 곧 종류라서요.

    ``client_id`` 를 넘기면 그 번호를 씁니다: 인바운드 고객은 문의 시점에 이미 번호를 받아
    두었으므로, Won 전환 건은 그 번호로 들어옵니다.
    """
    if not company.strip():
        raise HTTPException(status_code=400, detail="고객사를 입력해 주세요")
    with SessionLocal() as session:
        given = _int(client_id)
        if given is None:
            if customer_type not in ALLOCATABLE_BANDS:
                raise HTTPException(status_code=400, detail="고객 종류를 골라 주세요")
            given = next_client_id(session, customer_type)
        elif session.get(Client, given) is not None:
            raise HTTPException(status_code=400, detail=f"Client ID {given} 는 이미 있습니다")

        session.add(
            Client(
                client_id=given,
                company=company.strip(),
                industry=_text(industry),
                country=_text(country),
                department=DEPARTMENT_BY_TYPE.get(customer_type),
                contact_name=_text(contact_name),
                contact_info=_text(contact_info),
                first_won_on=_text(first_won_on) or date.today().isoformat(),
                plan_status="세팅중",
                owner=_text(owner) or actor_name(request, fallback="") or None,
            )
        )
        session.commit()
    return {"client_id": given}


@router.put("/won-customers/{client_id}")
async def update_client(
    client_id: int,
    company: str = Form(""),
    industry: str = Form(""),
    country: str = Form(""),
    department: str = Form(""),
    contact_name: str = Form(""),
    contact_info: str = Form(""),
    first_won_on: str = Form(""),
    plan_status: str = Form(""),
    owner: str = Form(""),
):
    with SessionLocal() as session:
        client = session.get(Client, client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        if company.strip():
            client.company = company.strip()
        client.industry = _text(industry)
        client.country = _text(country)
        client.department = _text(department)
        client.contact_name = _text(contact_name)
        client.contact_info = _text(contact_info)
        client.first_won_on = _text(first_won_on)
        if plan_status.strip():
            client.plan_status = plan_status.strip()
        client.owner = _text(owner)
        session.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 계약
# --------------------------------------------------------------------------- #
_CONTRACT_FIELDS = (
    "ticket_id", "deal_type", "starts_on", "ends_on", "currency", "payment_method",
    "payment_type", "first_payment_on", "billing_email", "note", "renewal_plan",
    "stop_reason", "memo", "revenue_from", "plan", "plan_name", "perso_email",
    "plan_starts_on", "plan_ends_on", "space_seq", "unit_currency",
)
_CONTRACT_INTS = ("installments", "invite_limit", "queue_limit", "concurrent_jobs", "space_count")
_CONTRACT_DECIMALS = ("amount_incl_vat", "amount_excl_vat", "unit_price", "unit_fx_rate")


def _fill_contract(contract: ClientContract, form: dict) -> None:
    for name in _CONTRACT_FIELDS:
        if name in form:
            setattr(contract, name, _text(form.get(name)))
    for name in _CONTRACT_INTS:
        if name in form:
            setattr(contract, name, _int(form.get(name)))
    for name in _CONTRACT_DECIMALS:
        if name in form:
            setattr(contract, name, _number(form.get(name)))
    if "doc_types" in form:
        # 복수 선택. 화면은 " + " 로 이어 보여주지만 저장은 배열입니다 — 문자열로 두면
        # "직접 계약 / DocuSign + 세금계산서 발행" 을 다시 쪼개야 필터가 됩니다.
        raw = (form.get("doc_types") or "").strip()
        contract.doc_types = [part.strip() for part in raw.split("|") if part.strip()] or None
    contract.deal_type = contract.deal_type or "MRR"
    contract.currency = contract.currency or "KRW"
    _apply_credits(contract)


@router.post("/won-customers/{client_id}/contracts")
async def create_contract(client_id: int, request: Request):
    """계약 추가. 차수는 받지 않고 **마지막 차수 + 1** 입니다."""
    form = dict(await request.form())
    with SessionLocal() as session:
        client = session.get(Client, client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        seq = max((c.seq for c in client.contracts), default=0) + 1
        contract = ClientContract(client_id=client_id, seq=seq)
        _fill_contract(contract, form)
        session.add(contract)
        session.flush()
        _seed_schedules(session, contract)
        session.commit()
        contract_id = contract.id
    return {"id": contract_id, "seq": seq}


def _seed_schedules(session, contract: ClientContract) -> None:
    """분납·크레딧 회차를 미리 깔아 둡니다 — 빈 목록이면 다음 결제일이 안 나옵니다.

    금액은 총액을 회차로 나눈 값이고, 날짜는 최초 결제일부터 한 달 간격입니다. 실제 일정이
    다르면 회차마다 고치면 됩니다. 깔아 두지 않으면 운영자가 12번 '추가'를 눌러야 합니다.
    """
    from decimal import Decimal

    count = max(1, contract.installments or 1)
    start = contract.first_payment_on or contract.starts_on
    base = date.fromisoformat(start) if start else None
    total = Decimal(str(contract.amount_incl_vat or 0))
    per = (total / count) if total else None
    for index in range(count):
        when = _add_months(base, index).isoformat() if base else None
        session.add(
            ContractPayment(
                contract_id=contract.id, no=index + 1, total=count, paid_on=when, amount=per
            )
        )
    credits = contract.credits or 0
    session.add(
        ContractCreditGrant(
            contract_id=contract.id,
            no=1,
            total=1,
            grant_on=contract.starts_on,
            amount=credits or None,
        )
    )


def _add_months(base: date, months: int) -> date:
    """n개월 뒤 같은 날. 그 달에 그 날이 없으면 말일입니다.

    dateutil 을 쓰지 않는 이유: 이 한 줄 때문에 의존성을 하나 더 들이면, 배포마다 그것도
    같이 설치되어야 합니다. 31일에 시작한 계약의 2월 회차가 28일이면 충분합니다.
    """
    import calendar

    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))


@router.put("/won-customers/contracts/{contract_id}")
async def update_contract(contract_id: int, request: Request):
    form = dict(await request.form())
    with SessionLocal() as session:
        contract = _get_contract(session, contract_id)
        _fill_contract(contract, form)
        session.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 크레딧 지급 회차
# --------------------------------------------------------------------------- #
@router.post("/won-customers/contracts/{contract_id}/credits")
async def add_credit_grant(
    contract_id: int,
    grant_on: str = Form(""),
    amount: str = Form(""),
    memo: str = Form(""),
):
    with SessionLocal() as session:
        contract = _get_contract(session, contract_id)
        existing = contract.credit_grants
        no = max((g.no for g in existing), default=0) + 1
        total = max(len(existing) + 1, max((g.total for g in existing), default=1))
        session.add(
            ContractCreditGrant(
                contract_id=contract_id,
                no=no,
                total=total,
                grant_on=_text(grant_on),
                amount=_int(amount),
                memo=_text(memo),
            )
        )
        for grant in existing:
            grant.total = total
        session.commit()
    return {"ok": True}


@router.put("/won-customers/credits/{grant_id}")
async def update_credit_grant(
    grant_id: int,
    request: Request,
    grant_on: str = Form(""),
    amount: str = Form(""),
    memo: str = Form(""),
    done: str = Form(""),
    granted_by: str = Form(""),
):
    with SessionLocal() as session:
        grant = session.get(ContractCreditGrant, grant_id)
        if grant is None:
            raise HTTPException(status_code=404, detail="지급 회차를 찾을 수 없습니다")
        grant.grant_on = _text(grant_on) or grant.grant_on
        if amount.strip():
            grant.amount = _int(amount)
        grant.memo = _text(memo)
        if done:
            was_done = grant.done
            grant.done = done == "true"
            # 지급자는 완료 건에만 남습니다. 취소하면 지운 사람 이름이 남아 있으면 안 됩니다.
            if grant.done and not was_done:
                grant.granted_by = _text(granted_by) or actor_name(request, fallback="") or None
            elif not grant.done:
                grant.granted_by = None
        session.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 결제 회차
# --------------------------------------------------------------------------- #
@router.post("/won-customers/contracts/{contract_id}/payments")
async def add_payment(
    contract_id: int, paid_on: str = Form(""), amount: str = Form("")
):
    with SessionLocal() as session:
        contract = _get_contract(session, contract_id)
        existing = contract.payments
        no = max((p.no for p in existing), default=0) + 1
        total = max(len(existing) + 1, max((p.total for p in existing), default=1))
        session.add(
            ContractPayment(
                contract_id=contract_id,
                no=no,
                total=total,
                paid_on=_text(paid_on),
                amount=_number(amount),
            )
        )
        for payment in existing:
            payment.total = total
        session.commit()
    return {"ok": True}


@router.put("/won-customers/payments/{payment_id}")
async def update_payment(
    payment_id: int,
    paid_on: str = Form(""),
    amount: str = Form(""),
    done: str = Form(""),
    fx_rate: str = Form(""),
):
    """입금 상태·날짜·금액. 완료로 바꾸면 **그 날짜의 환율**을 채웁니다.

    조회에 실패해도 저장은 됩니다 — 값이 비면 화면이 입력칸을 열어 둡니다. 환율 API 가
    죽었다고 입금 처리가 막히면, 그 사실은 수금율이 틀린 채로 며칠 지나서야 드러납니다.
    """
    with SessionLocal() as session:
        payment = session.get(ContractPayment, payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="결제 회차를 찾을 수 없습니다")
        if paid_on.strip():
            payment.paid_on = paid_on.strip()
        if amount.strip():
            payment.amount = _number(amount)
        if fx_rate.strip():
            payment.fx_rate = _number(fx_rate)
            payment.fx_on = payment.paid_on
        if done:
            payment.done = done == "true"
        if payment.done and payment.paid_on and payment.fx_rate is None:
            _fill_fx(payment)
        session.commit()
    return {"ok": True}


def _fill_fx(payment: ContractPayment) -> None:
    from ...integrations.fx import usd_krw_on

    try:
        found = usd_krw_on(payment.paid_on)
    except Exception:
        logger.warning("환율 조회 실패 (payment=%s).", payment.id, exc_info=True)
        return
    if found:
        payment.fx_rate, payment.fx_on = found


# --------------------------------------------------------------------------- #
# 클레임 · 히스토리
# --------------------------------------------------------------------------- #
@router.post("/won-customers/contracts/{contract_id}/claims")
async def add_claim(
    contract_id: int,
    kind: str = Form(...),
    happened_on: str = Form(""),
    compensation: str = Form(""),
    progress: str = Form("접수"),
):
    with SessionLocal() as session:
        _get_contract(session, contract_id)
        session.add(
            ContractClaim(
                contract_id=contract_id,
                kind=kind.strip(),
                happened_on=_text(happened_on) or date.today().isoformat(),
                compensation=_text(compensation),
                progress=progress.strip() or "접수",
            )
        )
        session.commit()
    return {"ok": True}


@router.put("/won-customers/claims/{claim_id}")
async def update_claim(
    claim_id: int,
    kind: str = Form(""),
    happened_on: str = Form(""),
    compensation: str = Form(""),
    progress: str = Form(""),
    action_on: str = Form(""),
):
    with SessionLocal() as session:
        claim = session.get(ContractClaim, claim_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="클레임을 찾을 수 없습니다")
        if kind.strip():
            claim.kind = kind.strip()
        claim.happened_on = _text(happened_on) or claim.happened_on
        claim.compensation = _text(compensation)
        if progress.strip():
            claim.progress = progress.strip()
            # 조치 완료로 바꿨는데 날짜가 없으면 오늘입니다. 완료인데 날짜가 빈 행은
            # "언제 끝났나" 에 답이 없습니다.
            if claim.progress == "조치 완료" and not (_text(action_on) or claim.action_on):
                claim.action_on = date.today().isoformat()
        if action_on.strip():
            claim.action_on = action_on.strip()
        session.commit()
    return {"ok": True}


@router.post("/won-customers/claims/{claim_id}/delete")
async def delete_claim(claim_id: int):
    with SessionLocal() as session:
        claim = session.get(ContractClaim, claim_id)
        if claim is not None:
            session.delete(claim)
            session.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 수주 전환 대기
# --------------------------------------------------------------------------- #
@router.post("/won-customers/pending/{pending_id}/dismiss")
async def dismiss_pending(pending_id: int):
    """보류. Won → Negotiating 롤백은 여기서 내리면 끝입니다."""
    with SessionLocal() as session:
        pending = session.get(PendingWon, pending_id)
        if pending is not None:
            pending.status = "dismissed"
            session.commit()
    return {"ok": True}
