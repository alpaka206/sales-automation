"""수주 고객 — 쓰기 라우트.

읽기는 ``ui_api`` 에 있습니다. 이 파일은 화면이 값을 바꿀 때 부르는 곳이고, 여기 있는 규칙은
전부 "저장하기 전에 한 번 더 계산한다" 입니다:

- **금액과 크레딧을 받고, 분당 단가는 계산합니다**(`won.unit_price`). 계약서에 적히는 것이
  그 둘이라서요. 방향이 반대였던 시절에는 반올림한 단가로 계산한 크레딧이 계약서의 크레딧과
  어긋났습니다.
- 통화가 쓰는 금액 칸은 하나뿐입니다: 원화는 공급가(총액은 +10% 로 계산), 그 외는 총액.
- 결제 회차를 입금 완료로 바꿀 때 **그 날짜의 환율**을 채웁니다. 조회에 실패하면 비워 둡니다;
  운영자가 직접 넣을 수 있고, 조회 실패가 저장을 막으면 안 됩니다.
- 계약 차수는 받지 않고 그 고객의 마지막 차수 + 1 입니다.

쓰기는 **전부 POST** 입니다. 콘솔의 쓰기 헬퍼(``postForm``)가 POST 하나만 보내는데 라우트가
PUT 이면 405 가 나고, 화면에는 "저장이 안 된다" 로만 보입니다 — 실제로 크레딧 지급 완료가
그렇게 막혀 있었습니다. 동사를 둘 두면 어느 쪽인지 매번 확인해야 하고, 그 확인을 한 번
빠뜨리면 같은 일이 반복됩니다.

계약 삭제는 없습니다. 지워야 할 계약은 실수로 만든 것뿐인데, 그건 값을 고치면 되고 — 지우면
거기 딸린 결제·크레딧 기록이 같이 사라집니다.
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request

from ...agents.client_ids import next_client_id
from ...common import won
from ...common.won import (
    ALLOCATABLE_BANDS,
    DEPARTMENT_BY_TYPE,
)
from ...db.models import (
    Client,
    ClientContract,
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


def _settle_amounts(contract: ClientContract) -> None:
    """계약이 쓰지 않는 금액 칸을 비웁니다 — **쓰는 쪽에 값이 있을 때만.**

    어느 칸을 쓰는지는 통화와 「VAT 포함/제외」가 함께 정합니다: VAT 제외로 적힌 원화
    계약만 공급가를 받고 총액을 +10% 로 계산하며(`won.total_amount`), 나머지(총액으로
    적힌 원화 계약, 그리고 부가세가 없는 그 외 통화)는 총액만 받습니다. 안 쓰는 쪽에 옛
    값이 남아 있으면, 통화나 기준을 바꾼 계약에서 화면이 계산한 값과 행에 든 값이
    갈라집니다.

    **조건이 붙어 있는 이유는 데이터가 사라졌기 때문입니다.** 이 라우트는 폼에 온 칸만
    건드리므로(`_fill_contract` 의 `if name in form`) 몇 칸만 보내는 폼도 받습니다.
    실제로 「갱신 계획·사용 중단 이유·비고」 패널이 세 칸만 보냈고, 그때 금액은 폼에 없어
    행의 값이 그대로 남는데 조건 없이 반대쪽을 비우니 **총액만 있던 옛 원화 계약은 비고
    한 줄 저장에 금액이 통째로 사라졌습니다.** 되돌릴 방법이 없습니다. 그 패널은 이제
    없지만(이관 0073) 조건은 남깁니다 — 부분 폼은 한 줄이면 다시 생깁니다.

    쓰는 쪽에 값이 있을 때만 반대쪽을 지우면, 옛 계약은 다음번에 금액을 실제로 채워
    저장할 때 제자리를 찾습니다.
    """
    if not won.vat_applicable(contract):
        # 부가세가 없으면 금액은 하나입니다. 그 하나는 `amount_incl_vat` 에 삽니다 —
        # `won.total_amount` 가 미해당 계약에서 읽는 칸이고, 「포함」이라는 이름은 부가세가
        # 없는 계약에서는 그냥 「그 금액」이라는 뜻입니다.
        if contract.amount_incl_vat is None and contract.amount_excl_vat is not None:
            contract.amount_incl_vat = contract.amount_excl_vat
        if contract.amount_incl_vat is not None:
            contract.amount_excl_vat = None
        return

    # 해당이면 **둘 다 저장합니다.** 다만 받은 값을 그대로 두 칸에 두지 않고, 고른 기준에서
    # 나머지를 **다시 계산합니다.** 화면이 자동완성해 주더라도 폼은 두 숫자를 따로 보내므로,
    # 손으로 한쪽만 고친 요청이 들어오면 두 값이 어긋난 채 저장됩니다 — 그리고 분당 단가와
    # 총액이 서로 다른 금액에서 나옵니다. 기준 한 칸에서 파생시키면 그 상태가 아예 없습니다.
    basis = won.billing_amount(contract)
    if basis is None:
        return
    if won.vat_included(contract):
        contract.amount_incl_vat = basis
        contract.amount_excl_vat = basis / (1 + won.VAT_RATE)
    else:
        contract.amount_excl_vat = basis
        contract.amount_incl_vat = basis * (1 + won.VAT_RATE)


# --------------------------------------------------------------------------- #
# 고객
# --------------------------------------------------------------------------- #
@router.post("/won-customers")
async def create_client(request: Request):
    """새 고객 — **첫 계약과 함께**. 고객만 있는 순간이 존재하지 않습니다.

    Client ID 는 고객 종류가 정합니다(번호대가 곧 종류). ``client_id`` 를 넘기면 그 번호를
    씁니다: 인바운드 고객은 문의 시점에 이미 번호를 받아 두었으므로 Won 전환 건은 그 번호로
    들어옵니다.

    **계약을 같이 받는 것이 이 라우트의 요점입니다.** 예전에는 화면이 여기서 고객을 먼저
    만들고 계약 폼으로 갔습니다. 그 사이에 폼을 닫으면 계약이 0건인 고객이 남았고,
    ``won.plan_status`` 가 그런 고객을 「세팅중」으로 읽어 워크북 「고객 기본 정보」에
    세팅중 행으로 실어 보냈습니다. 대기 카드를 누를 때마다 하나씩 늘었고 지울 길이 없어서
    운영자가 「왜 계속 추가되냐」고 물었습니다(2026-08-25).

    고친 곳이 폼이 아니라 **라우트**인 이유: 고객만 만드는 길이 남아 있는 한 다른 화면이
    언젠가 또 그리로 갑니다. 길을 없애면 그 상태가 아예 만들어지지 않습니다.
    """
    form = dict(await request.form())
    company = (form.get("company") or "").strip()
    if not company:
        raise HTTPException(status_code=400, detail="고객사를 입력해 주세요")
    if not _text(form.get("starts_on")):
        raise HTTPException(status_code=400, detail="첫 계약 정보를 함께 입력해 주세요")
    customer_type = (form.get("customer_type") or "").strip()
    with SessionLocal() as session:
        given = _int(form.get("client_id"))
        if given is None:
            if customer_type not in ALLOCATABLE_BANDS:
                raise HTTPException(status_code=400, detail="고객 종류를 골라 주세요")
            given = next_client_id(session, customer_type)
        elif session.get(Client, given) is not None:
            raise HTTPException(status_code=400, detail=f"Client ID {given} 는 이미 있습니다")

        client = Client(
            client_id=given,
            company=company,
            industry=_text(form.get("industry")),
            country=_text(form.get("country")),
            # 담당부서는 **번호대**에서 나옵니다. 화면이 보낸 고객 종류를 그대로 믿으면,
            # 대기 건이 물고 온 3000·4000번대 번호에 GTM 이 박히고 — `won.department` 는
            # 저장된 값이 이기므로 — 그 고객의 매출이 남의 부서 묶음으로 들어갑니다.
            department=DEPARTMENT_BY_TYPE.get(won.client_type(given)),
            contact_name=_text(form.get("contact_name")),
            contact_info=_text(form.get("contact_info")),
            first_won_on=_text(form.get("first_won_on")) or date.today().isoformat(),
            owner=_text(form.get("owner")) or actor_name(request, fallback="") or None,
        )
        session.add(client)
        session.flush()
        contract_id, seq = _add_contract(session, client, form)
        session.commit()
    return {"client_id": given, "id": contract_id, "seq": seq}


@router.post("/won-customers/{client_id}")
async def update_client(
    client_id: int,
    company: str = Form(""),
    industry: str = Form(""),
    country: str = Form(""),
    department: str = Form(""),
    contact_name: str = Form(""),
    contact_info: str = Form(""),
    first_won_on: str = Form(""),
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
        client.owner = _text(owner)
        session.commit()
    return {"ok": True}


@router.post("/won-customers/{client_id}/retire")
async def retire_client(client_id: int, retire: str = Form("1")):
    """계약이 없는 고객을 **장부에서 내립니다.** 행도 번호도 그대로 둡니다.

    삭제와 무엇이 다른가: 삭제는 「이 번호는 잘못 만든 것」이라 번호까지 걷어냅니다(중복으로
    발급된 번호를 정리하는 길). 내리기는 「이 고객은 활성이 아니다」일 뿐이라 번호가 남습니다 —
    그 번호를 문의·연락처가 들고 있고, 워크북의 계약·회차 탭과 Inbound DB 가 그 행을 조회해
    회사명을 가져오므로 지우면 그 조회가 통째로 빕니다.

    Won 에 잘못 올라갔다가 다른 단계로 옮겨진 건이 이쪽입니다. 자동으로도 내려갑니다
    (`stage_sync._retire_empty_client`) — 이 라우트는 그 자동 처리가 못 잡은 건과, 되돌리는
    길입니다.

    **계약이 있으면 거부합니다.** 계약이 있는 고객은 활성이고, 내려야 할 이유가 있다면 그건
    계약 종료일이 말할 일입니다(`won.plan_status`).

    되돌리려면 ``retire=0`` 을 보냅니다. **빈 문자열이 아닙니다** — 빈 폼 값은 중간에서
    통째로 사라지는 일이 있어서(httpx 가 그렇습니다) 「해제」가 조용히 「내리기」가 됩니다.
    끄는 것은 끄는 값으로 말해야 합니다.
    """
    with SessionLocal() as session:
        client = session.get(Client, client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        if client.contracts:
            raise HTTPException(
                status_code=400,
                detail=f"계약 {len(client.contracts)}건이 있는 고객은 내릴 수 없습니다",
            )
        client.retired_on = date.today().isoformat() if _flag(retire) else None
        session.commit()
    return {"ok": True}


@router.post("/won-customers/{client_id}/delete")
async def delete_client(client_id: int):
    """계약이 하나도 없는 고객을 지웁니다 — 잘못 만들어진 번호를 콘솔에서 되돌리는 길.

    **계약이 있으면 거부합니다.** 거기 딸린 결제·크레딧·인식 매출이 같이 사라지고 되돌릴
    방법이 없습니다. 그건 지울 일이 아니라 고칠 일입니다(모듈 첫머리의 「계약 삭제는
    없습니다」와 같은 이유). 그래서 이 라우트는 「고객을 지운다」가 아니라 「빈 고객을
    치운다」입니다.

    그 번호를 가리키던 문의·연락처·전환 대기의 Client ID 도 같이 비웁니다. 없는 번호를
    들고 있으면 다음 Won 때 ``find_existing_client_id`` 가 그 번호를 도로 찾아 주고, 지운
    고객이 살아 돌아옵니다.

    **워크북의 행은 건드리지 않습니다.** 동기화는 지금 있는 고객의 행만 정리하므로
    (``won_sheets.collect_rows`` 의 ``managed``), 지워진 번호의 행은 「손으로 쓴 행」으로
    보여 그대로 남습니다 — 그 탭은 운영자의 것이고, 콘솔이 모르는 행을 지우기 시작하면
    운영자가 먼저 채워 둔 회사가 사라집니다. 그 한 줄은 손으로 지웁니다.
    """
    from ...db.models import Contact, Conversation

    with SessionLocal() as session:
        client = session.get(Client, client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        if client.contracts:
            raise HTTPException(
                status_code=400,
                detail=f"계약 {len(client.contracts)}건이 있는 고객은 지울 수 없습니다",
            )
        for model in (Contact, Conversation):
            session.query(model).filter(model.sheet_client_id == client_id).update(
                {"sheet_client_id": None}, synchronize_session=False
            )
        session.query(PendingWon).filter(PendingWon.client_id == client_id).update(
            {"client_id": None}, synchronize_session=False
        )
        session.delete(client)
        session.commit()
    logger.info("계약이 없는 수주 고객 %s 를 지웠습니다", client_id)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 계약
# --------------------------------------------------------------------------- #
_CONTRACT_FIELDS = (
    "ticket_id", "deal_type", "starts_on", "ends_on", "currency", "payment_method",
    "payment_type", "first_payment_on", "billing_email", "note",
    "revenue_from", "plan", "plan_name", "perso_email",
    "plan_starts_on", "plan_ends_on", "space_seq",
    # 중도 해지일. 플랜은 만료일과 이 날짜 중 빠른 쪽에서 끝납니다.
    "terminated_on", "fx_on",
)
# credits 가 여기 있는 이유: 계약 크레딧은 이제 **입력**입니다. 계약서에 적히는 것이
# 금액과 크레딧이고, 분당 단가가 그 둘에서 나옵니다(won.unit_price).
_CONTRACT_INTS = (
    "credits", "installments", "invite_limit", "queue_limit", "concurrent_jobs", "space_count",
    # 예상 환불 금액의 분자. 수동 입력입니다 — 제품 쪽에서 가져오는 경로가 아직 없습니다.
    "credits_used",
)
_CONTRACT_DECIMALS = ("amount_incl_vat", "amount_excl_vat", "fx_rate")


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
    # 금액 칸과 **같이** 와야 합니다. 안 그러면 「비고 한 줄」만 보내는 폼이 기준을
    # 뒤집습니다(그 폼은 금액을 안 보냅니다 — `_one_amount_per_currency` 의 주석 참고).
    if "vat_included" in form:
        contract.vat_included = _flag(form.get("vat_included"))
    # **금액 칸보다 먼저 정해져야 하는 값입니다.** 부가세가 붙는 계약인지에 따라 아래
    # `_settle_amounts` 가 한 칸을 남길지 두 칸을 채울지가 갈립니다.
    if "vat_applicable" in form:
        contract.vat_applicable = _flag(form.get("vat_applicable"))
    if "doc_types" in form:
        # 복수 선택. 화면은 " + " 로 이어 보여주지만 저장은 배열입니다 — 문자열로 두면
        # "직접 계약 / DocuSign + 세금계산서 발행" 을 다시 쪼개야 필터가 됩니다.
        raw = (form.get("doc_types") or "").strip()
        contract.doc_types = [part.strip() for part in raw.split("|") if part.strip()] or None
    contract.deal_type = contract.deal_type or "MRR"
    contract.currency = contract.currency or "KRW"
    # **플랜 기간은 계약 기간과 다른 것입니다** (2026-08-31 운영자 지시). 계약은 먼저 맺고
    # 실제 사용은 늦게 시작하는 일이 흔한데, 한동안 폼이 묻지 않고 계약 날짜를 그대로
    # 복사했습니다 — 그래서 MRR 도 「사용중」도 계약 기간으로 계산되고 있었습니다.
    #
    # 이 두 줄은 이제 **기본값**입니다: 비워 두면 계약 기간과 같다는 뜻이고, 그게 대부분의
    # 계약입니다. 폼이 값을 보내면 그대로 저장됩니다. 옛 행과 워크북에서 온 행도 이 기본값
    # 덕분에 빈 채로 남지 않습니다.
    contract.plan_starts_on = contract.plan_starts_on or contract.starts_on
    contract.plan_ends_on = contract.plan_ends_on or contract.ends_on
    _fill_contract_fx(contract)
    _settle_amounts(contract)


def _complete_pending_won(session, pending_id: int | None, client: Client) -> None:
    """계약 저장과 같은 트랜잭션에서 선택한 전환 대기를 끝냅니다."""
    if pending_id is None:
        return
    pending = session.get(PendingWon, pending_id)
    if pending is None or pending.status != "pending":
        return
    if pending.client_id is not None and pending.client_id != client.client_id:
        raise HTTPException(
            status_code=409,
            detail="수주 전환 대기의 Client ID와 계약 고객이 다릅니다",
        )
    _close_pending(session, pending, client)


def _claim_ticket(session, contract: ClientContract, client: Client) -> None:
    """계약에 적힌 티켓의 전환 대기를 내려놓습니다 — 그 티켓은 이제 이 고객 것입니다.

    운영자가 계약에 티켓 번호를 적는 순간 「계약 정보를 입력해야 합니다」는 끝납니다.
    대기 카드에서 넘어온 건은 ``pending_id`` 로 닫히지만, 이미 장부에 있던 고객의 계약에
    티켓을 **손으로** 적은 경우에는 닫을 사람이 없어 같은 티켓이 대기에 그대로 남았습니다.

    ``_complete_pending_won`` 과 달리 Client ID 가 달라도 **막지 않습니다.** 어느 고객의
    티켓인지는 방금 운영자가 적어서 정한 것이고, 대기 행의 번호는 문의 시점의 추정입니다.
    티켓 칸을 비우면 아무 일도 하지 않습니다: 연동을 푸는 것이지 대기를 되살리는 것이
    아닙니다.

    **여기서 옮겨지는 것은 대기 행의 번호뿐입니다.** 문의·연락처의 ``sheet_client_id`` 는
    ``_close_pending`` 이 비어 있을 때만 채우므로, 이미 다른 번호가 박혀 있으면 「한 회사에
    번호가 둘」인 상태는 남습니다. 그 둘을 진짜로 합치는 것은
    ``client_merge.merge_client_ids`` 입니다 — 네 테이블과 워크북을 같이 옮기고, 양쪽에
    저장된 회사명이 전부 같아야 통과합니다.
    """
    ticket_id = (contract.ticket_id or "").strip()
    if not ticket_id:
        return
    rows = (
        session.query(PendingWon)
        .filter(PendingWon.ticket_id == ticket_id, PendingWon.status == "pending")
        .all()
    )
    for pending in rows:
        _close_pending(session, pending, client)


def _close_pending(session, pending: PendingWon, client: Client) -> None:
    """대기 한 줄을 끝내고 그 문의·연락처를 이 고객에 묶습니다."""
    pending.client_id = client.client_id
    pending.status = "done"
    if pending.conversation_id:
        from ...db.models import Contact, Conversation

        conversation = session.get(Conversation, pending.conversation_id)
        if conversation is not None:
            if conversation.sheet_client_id is None:
                conversation.sheet_client_id = client.client_id
            contact = (
                session.get(Contact, conversation.contact_id)
                if conversation.contact_id
                else None
            )
            if contact is not None and contact.sheet_client_id is None:
                contact.sheet_client_id = client.client_id
            if client.contact_id is None and contact is not None:
                client.contact_id = contact.id


def _flag(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "on", "yes"}


def _fill_contract_fx(contract: ClientContract) -> None:
    """환율이 비어 있으면 **계약일 고시가**로 채웁니다. 적어 보냈으면 그 값 그대로.

    왜 계약에 박아 두는가: 예상 MRR 이 오늘 고시가로 환산하던 시절에는 같은 계약의 지난달
    매출이 이번 달에 달라 보였습니다. 계약에 박아 두면 언제 봐도 같은 숫자입니다.

    원화 계약은 환산할 것이 없어 조회하지 않습니다. 조회가 실패해도 저장을 막지 않습니다 —
    비어 있으면 화면이 예전처럼 오늘 고시가로 환산하고, 운영자가 나중에 손으로 적을 수
    있습니다. 계약 하나 저장하자고 외부 API 한 번에 매달릴 이유가 없습니다.
    """
    if won.is_krw(contract) or contract.fx_rate is not None:
        return
    day = contract.starts_on or contract.first_payment_on
    if not day:
        return
    try:
        from ...integrations import fx

        found = fx.usd_krw_on(day)
    except Exception:
        logger.warning("계약 환율 조회 실패 (client=%s seq=%s)", contract.client_id, contract.seq)
        return
    if found:
        contract.fx_rate, contract.fx_on, _source = found


def _add_contract(session, client: Client, form: dict) -> tuple[int, int]:
    """계약 한 건 + 회차 + 전환 대기 정리. 신규 고객과 기존 고객이 같은 길을 지납니다.

    차수는 받지 않고 **마지막 차수 + 1** 입니다.
    """
    # 계약이 들어오면 그 순간 다시 장부에 올라옵니다. 내려 둔 고객에 계약을 넣고도
    # 목록에 안 보이면, 운영자는 저장이 안 된 줄 압니다.
    client.retired_on = None
    seq = max((c.seq for c in client.contracts), default=0) + 1
    contract = ClientContract(client_id=client.client_id, seq=seq)
    _fill_contract(contract, form)
    # 「저장 후 플랜 상태」 칸이 여기 있었습니다. 이제 플랜 상태는 계약 기간에서
    # 나오므로(won.plan_status), 첫 계약을 넣는 순간이 곧 세팅중에서 사용중으로
    # 넘어가는 순간입니다 — 따로 고를 것이 없습니다.
    session.add(contract)
    session.flush()
    _seed_schedules(
        session,
        contract,
        credit_rounds=_int(form.get("credit_rounds")) or 1,
        first_credit_on=_text(form.get("first_credit_on")),
    )
    _complete_pending_won(session, _int(form.get("pending_id")), client)
    _claim_ticket(session, contract, client)
    return contract.id, seq


@router.post("/won-customers/{client_id}/contracts")
async def create_contract(client_id: int, request: Request):
    """기존 고객에 계약 추가. 새 고객의 첫 계약은 ``POST /won-customers`` 가 같이 만듭니다."""
    form = dict(await request.form())
    with SessionLocal() as session:
        client = session.get(Client, client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
        contract_id, seq = _add_contract(session, client, form)
        session.commit()
    return {"id": contract_id, "seq": seq}


def _seed_schedules(
    session,
    contract: ClientContract,
    credit_rounds: int = 1,
    first_credit_on: str = "",
) -> None:
    """분납·크레딧 회차를 미리 깔아 둡니다 — 빈 목록이면 다음 결제일이 안 나옵니다.

    금액은 총액을 회차로 나눈 값이고, 날짜는 최초 결제일부터 한 달 간격입니다. 실제 일정이
    다르면 회차마다 고치면 됩니다. 깔아 두지 않으면 운영자가 12번 '추가'를 눌러야 합니다.
    """
    from decimal import Decimal

    count = max(1, contract.installments or 1)
    start = contract.first_payment_on or contract.starts_on
    base = date.fromisoformat(start) if start else None
    total = won.total_amount(contract) or Decimal(0)
    per = (total / count) if total else None
    for index in range(count):
        when = _add_months(base, index).isoformat() if base else None
        session.add(
            ContractPayment(
                contract_id=contract.id, no=index + 1, total=count, paid_on=when, amount=per
            )
        )
    # 크레딧도 같은 이유로 회차를 깔아 둡니다. 나눗셈의 나머지는 **마지막 회차**에 붙입니다 —
    # 회차마다 반올림하면 합계가 계약 크레딧과 어긋나고, 그 차이는 화면에서 안 보입니다.
    rounds = max(1, credit_rounds)
    credits = contract.credits or 0
    per = credits // rounds if credits else 0
    # 첫 지급일은 폼이 받습니다. 계약 시작일과 다른 계약이 흔합니다 — 세팅 기간을 두고
    # 다음 달 1일부터 주는 식으로. 안 주면 예전처럼 계약 시작일부터입니다.
    base_credit = first_credit_on or contract.starts_on
    start_date = date.fromisoformat(base_credit) if base_credit else None
    for index in range(rounds):
        amount = per if index < rounds - 1 else credits - per * (rounds - 1)
        session.add(
            ContractCreditGrant(
                contract_id=contract.id,
                no=index + 1,
                total=rounds,
                grant_on=_add_months(start_date, index).isoformat() if start_date else None,
                amount=amount or None,
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


@router.post("/won-customers/contracts/{contract_id}")
async def update_contract(contract_id: int, request: Request):
    form = dict(await request.form())
    with SessionLocal() as session:
        contract = _get_contract(session, contract_id)
        _fill_contract(contract, form)
        client = session.get(Client, contract.client_id)
        if client is not None:
            _claim_ticket(session, contract, client)
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


@router.post("/won-customers/credits/{grant_id}")
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


@router.post("/won-customers/payments/{payment_id}")
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
        payment.fx_rate, payment.fx_on, _source = found


# --------------------------------------------------------------------------- #
# 시트 → DB (한 번만 쓰는 방향)
# --------------------------------------------------------------------------- #
# 경로가 ``/won-customers`` 밖에 있는 것은 일부러입니다. 그 접두사는 브라우저 세션 쿠키로
# 열리는데, 이건 사람이 화면에서 누르는 것이 아니라 밖에서 한 번 부르는 작업이라
# ``X-Internal-Token`` 문을 지나야 합니다(main.py 의 인증 미들웨어).
#
# 왜 라우트가 필요했나: 값이 시트에만 있는 상태에서 DB 를 처음 채워야 하는데, 사내망이
# Postgres 포트를 막고 Render 무료 플랜은 셸도 일회성 작업도 안 됩니다. 배포본에서 코드를
# 돌릴 수 있는 통로가 HTTP 뿐이었습니다. 자연키로 맞추므로 여러 번 불러도 안전합니다.
@router.post("/internal/won-customers/import-from-sheet")
async def import_from_sheet_route(write: str = Form("")):
    from ...agents.sheet_to_db import import_from_sheet

    return import_from_sheet(write=write == "true")


# --------------------------------------------------------------------------- #
# 내보내기
# --------------------------------------------------------------------------- #
@router.get("/won-customers/export.csv")
def export_csv():
    """수주 고객 전체를 계약 한 건당 한 줄로.

    시트로 옮겨 붙이는 것이 목적이라 **계산된 값도 같이** 내보냅니다(계약 개월수·월간
    매출·누적 지급·수금 완료). 시트에서 다시 수식을 짜면 두 곳의 숫자가 갈라집니다.

    BOM 을 붙입니다 — 없으면 Excel 이 UTF-8 을 저는 코드페이지로 읽어 한글이 깨집니다.
    """
    import csv
    import io
    from datetime import date

    from fastapi.responses import StreamingResponse
    from sqlalchemy.orm import selectinload

    from ...common import won

    today = date.today()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Client ID", "고객사", "고객 종류", "산업 분야", "국가", "담당부서",
        "고객 담당자", "고객 연락처", "최초 수주일", "플랜 상태", "담당",
        "계약 차수", "계약 상태", "Ticket ID", "수주 유형",
        "계약 시작일", "계약 종료일", "계약 개월수", "계약서 유형",
        "계약 크레딧", "누적 지급 크레딧", "통화",
        "총 계약금액 (VAT 포함)", "공급가 (VAT 제외)", "수금 완료 금액", "수금율",
        "분당 단가",
        "결제 수단", "결제 방식", "총 분납 횟수", "최초 결제일", "Billing Email",
        "월간 매출 (VAT 포함)", "매출 인식 시작 월",
        "플랜", "플랜명", "Perso Email", "Space 개수", "space_seq",
        "다음 크레딧 지급일", "다음 결제일",
    ])
    with SessionLocal() as session:
        clients = (
            session.query(Client)
            .options(
                selectinload(Client.contracts).selectinload(ClientContract.credit_grants),
                selectinload(Client.contracts).selectinload(ClientContract.payments),
            )
            .order_by(Client.client_id)
            .all()
        )
        for client in clients:
            base = [
                client.client_id, client.company, won.client_type(client.client_id),
                client.industry, client.country, client.department,
                client.contact_name, client.contact_info, client.first_won_on,
                won.plan_status(client, today), client.owner,
            ]
            if not client.contracts:
                # 계약이 아직 없는 고객도 한 줄 나갑니다 — 빠지면 명단이 아닙니다.
                writer.writerow(base + [""] * 30)   # 머리글 41 − 고객 11
                continue
            for contract in client.contracts:
                total = float(won.total_amount(contract) or 0)
                paid = float(won.collected(contract))
                grant = won.next_credit_grant(contract)
                payment = won.next_payment(contract)
                writer.writerow(base + [
                    contract.seq, won.contract_state(contract, today), contract.ticket_id,
                    contract.deal_type, contract.starts_on, contract.ends_on,
                    won.months_between(contract.starts_on, contract.ends_on),
                    " + ".join(contract.doc_types or []),
                    contract.credits, won.granted_credits(contract), contract.currency,
                    # 공급가는 `supply_amount` 입니다 — `billing_amount` 는 **계약서에
                    # 적힌 금액**이라, VAT 포함으로 적힌 원화 계약에서는 총액을 돌려줍니다.
                    # 그 값을 「공급가 (VAT 제외)」 칸에 넣으면 과세표준이 10% 부풀고, 옆
                    # 칸의 총액이 그럴듯해서 아무도 눈치채지 못합니다. 화면·워크북과 같은
                    # 값이어야 합니다.
                    won.total_amount(contract), won.supply_amount(contract), paid,
                    f"{(paid / total * 100):.1f}%" if total else "",
                    won.unit_price(contract),
                    contract.payment_method, contract.payment_type, contract.installments,
                    contract.first_payment_on, contract.billing_email,
                    round(float(won.monthly_revenue(contract))),
                    won.revenue_start_month(contract),
                    contract.plan, contract.plan_name, contract.perso_email,
                    contract.space_count, contract.space_seq,
                    grant.grant_on if grant else "",
                    payment.paid_on if payment else "",
                ])

    body = "﻿" + buffer.getvalue()
    stamp = today.isoformat()
    return StreamingResponse(
        iter([body]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="won-customers-{stamp}.csv"'},
    )
