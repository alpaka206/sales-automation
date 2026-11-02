"""수주 전환 대기 → 계약 폼: 그 티켓의 메일로 계약 칸을 미리 채웁니다 (2026-11-02 운영자).

**아무것도 저장하지 않습니다.** 채운 값은 폼의 빈 칸으로 가고, 등록은 사람이 확인하고 저장을 누를 때입니다
(`won_customers` — 「계약 정보는 사람이 채운다」는 그대로입니다). 모델은 칸과 근거 문장을 내고, 이 파일이 값의 모양과
근거가 메일에 정말 있는지를 봅니다 — 어긋나면 그 칸은 비웁니다. 빈 칸은 사람이 채우지만 틀린 칸은 못 보고 저장됩니다.

어느 칸을 채울지는 2026-11-02 시험으로 정했습니다(운영 사본, 정답은 등록된 계약, 메일이 있는 계약 6건 × 2회 — 의미
있는 숫자는 3건에서 나왔고 전부 달러 계약이라 VAT 는 시험되지 않았습니다). 종료일과 최초 결제일은 틀려서 뺐습니다
(1년 · 하루 차이). 틀리면 MRR 이 같이 틀립니다.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel
from sqlalchemy import func, select

from ..common import won
from ..db.models import ClientContract, Conversation, PendingWon
from ..db.session import SessionLocal
from .inbound import threads_events
from .ticket_history import is_our_address

_MAIL_CHANNELS = ("이메일", "email")
_MAIL_ROLES = ("customer", "sales")
# 큰 스레드의 앞부분만 잘리도록 메일 하나와 전체에 상한을 둔다 — 합의는 대개 뒤쪽에 있다.
_MAIL_CHARS = 6_000
_THREAD_CHARS = 60_000
_EVIDENCE_CHARS = 200
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


class _Cell(BaseModel):
    # 모델은 숫자를 숫자로 준다 — 글자만 받으면 금액이 든 답이 전부 검증에서 떨어진다(시험에서 그랬다).
    value: str | int | float | None = None
    evidence: str | None = None


class _Extracted(BaseModel):
    deal_type: _Cell = _Cell()
    starts_on: _Cell = _Cell()
    currency: _Cell = _Cell()
    amount_incl_vat: _Cell = _Cell()
    credits: _Cell = _Cell()
    payment_method: _Cell = _Cell()
    payment_type: _Cell = _Cell()
    installments: _Cell = _Cell()
    billing_email: _Cell = _Cell()
    contact_name: _Cell = _Cell()
    contact_info: _Cell = _Cell()
    plan: _Cell = _Cell()
    perso_email: _Cell = _Cell()


def _flat(text: str) -> str:
    return " ".join(text.split()).casefold()


def _positive(raw: str) -> Decimal | None:
    try:
        number = Decimal(raw.replace(",", "").strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() and number > 0 else None


def _whole(number: Decimal) -> str:
    return format(number.normalize(), "f")


def _customer_address(raw: str) -> str | None:
    """고객 쪽 메일이어야 하는 칸 — 우리 주소가 들어오면 그 메일의 서명을 잘못 읽은 것이다."""
    return raw if _EMAIL.fullmatch(raw) and not is_our_address(raw) else None


def _checked(name: str, raw: str, evidence: str) -> str | None:
    """칸의 모양에 맞는 값 — 안 맞으면 None(그 칸은 비운다)."""
    if name == "deal_type":
        return raw if raw in won.DEAL_TYPES else None
    if name == "currency":
        return raw if raw in won.CURRENCIES else None
    if name == "payment_method":
        return raw if raw in won.PAYMENT_METHODS else None
    if name == "payment_type":
        return raw if raw in won.PAYMENT_TYPES else None
    if name == "plan":
        return raw if raw in won.PLANS else None
    if name == "starts_on":
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            return None
        # 연도가 근거에 안 적혔으면 모델이 지은 연도다 — 시험에서 종료일이 그렇게 1년 어긋났다.
        return raw if str(day.year) in evidence else None
    if name == "amount_incl_vat":
        number = _positive(raw)
        return _whole(number) if number else None
    if name == "credits":
        number = _positive(raw)
        return _whole(number) if number and number == number.to_integral_value() else None
    if name == "installments":
        number = _positive(raw)
        return _whole(number) if number and number == number.to_integral_value() and number <= 120 else None
    if name in ("billing_email", "perso_email"):
        return _customer_address(raw) if len(raw) <= 255 else None
    if name == "contact_name":
        return raw if len(raw) <= 120 else None
    if name == "contact_info":
        return raw if len(raw) <= 255 and not (_EMAIL.fullmatch(raw) and is_our_address(raw)) else None
    return None


def _fields(result: _Extracted, thread: str) -> dict[str, dict[str, str]]:
    source = _flat(thread)
    fields: dict[str, dict[str, str]] = {}
    for name in _Extracted.model_fields:
        cell: _Cell = getattr(result, name)
        raw = "" if cell.value is None else str(cell.value).strip()
        evidence = " ".join((cell.evidence or "").split())
        # 근거가 메일에 없으면 그 값은 모델이 지은 것이다.
        if not raw or not evidence or _flat(evidence) not in source:
            continue
        value = _checked(name, raw, evidence)
        if value is not None:
            fields[name] = {"value": value, "evidence": evidence[:_EVIDENCE_CHARS]}
    return fields


def _conversation_ids(session, pending: PendingWon) -> list[int]:
    """읽을 대화 — 이 티켓과, 처음 계약이면 같은 사람의 다른 문의.

    메일은 그 사람의 가장 최근 티켓에 붙기도 해서(`mailbox_sync`) 협상이 다른 문의에 있을 수 있다. 재계약이면 이 티켓만
    읽는다 — 같은 사람의 옛 문의에는 지난 계약의 금액과 날짜가 있다. 회사 도메인으로는 안 묶는다.
    """
    conversation = session.get(Conversation, pending.conversation_id) if pending.conversation_id else None
    if conversation is None:
        conversation = session.scalar(
            select(Conversation).where(Conversation.hubspot_ticket_id == pending.ticket_id)
        )
    if conversation is None:
        return []
    renewal = bool(pending.client_id) and bool(session.scalar(
        select(func.count()).select_from(ClientContract).where(ClientContract.client_id == pending.client_id)
    ))
    if renewal:
        return [conversation.id]
    return list(session.scalars(select(Conversation.id).where(Conversation.contact_id == conversation.contact_id)))


def _thread_text(turns: list) -> str:
    lines = [
        f"[{turn.at:%Y-%m-%d} {'고객' if turn.direction == 'inbound' else '우리'}] 제목: {turn.subject or ''}\n"
        f"{(turn.body or '')[:_MAIL_CHARS]}"
        for turn in turns
    ]
    return "\n\n".join(lines)[-_THREAD_CHARS:]


def suggest_contract(pending_id: int, *, llm=None) -> dict:
    """{status, mails, fields} — status 는 ok · no_mail. 대기 행이 없으면 LookupError, 모델이 실패하면 그 예외."""
    with SessionLocal() as session:
        pending = session.get(PendingWon, pending_id)
        if pending is None:
            raise LookupError(pending_id)
        conversation_ids = _conversation_ids(session, pending)
        events = threads_events(session, conversation_ids) if conversation_ids else {}

    seen: set[str] = set()
    turns = []
    for conversation_turns in events.values():
        for turn in conversation_turns:
            if (
                turn.source_ref not in seen
                and (turn.channel or "").lower() in _MAIL_CHANNELS
                and turn.role in _MAIL_ROLES
            ):
                seen.add(turn.source_ref)
                turns.append(turn)
    turns.sort(key=lambda turn: turn.at)
    if not turns:
        return {"status": "no_mail", "mails": 0, "fields": {}}

    from ..llm.client import LLMClient

    thread = _thread_text(turns)
    result = (llm or LLMClient()).complete(
        "inbound/fill_contract", {"thread_text": thread}, schema=_Extracted, tier="pro", max_tokens=4000
    )
    return {"status": "ok", "mails": len(turns), "fields": _fields(result, thread)}
