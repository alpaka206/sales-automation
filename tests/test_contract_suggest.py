"""수주 전환 대기 → 계약 폼: 메일로 칸을 채운다 — 아무것도 저장하지 않는다 (2026-11-02 운영자).

모델은 칸과 근거를 내고 코드가 값의 모양과 근거가 메일에 있는지를 본다. 빈 칸은 사람이 채우지만 틀린 칸은 못 보고 저장된다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.agents import contract_suggest
from src.agents.contract_suggest import _Extracted, suggest_contract
from src.db.base import Base
from src.db.models import Client, ClientContract, Contact, Conversation, CustomerInteraction, Message, PendingWon


def _ago(days: float) -> datetime:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    made = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(contract_suggest, "SessionLocal", made)
    return made


class _FakeModel:
    """모델 자리 — 받은 대화를 적어 두고 정해 둔 답을 돌려준다."""

    def __init__(self, **cells):
        self.answer = _Extracted(**{name: {"value": value, "evidence": evidence} for name, (value, evidence) in cells.items()})
        self.thread = None
        self.calls = 0

    def complete(self, prompt_name, variables=None, schema=None, tier=None, max_tokens=None):
        assert prompt_name == "inbound/fill_contract" and schema is _Extracted
        # 작은 한도는 생각 토큰에 먹힌다(`tests/test_llm_budgets.py`).
        assert max_tokens >= 2000
        self.thread, self.calls = variables["thread_text"], self.calls + 1
        return self.answer


def _lead(session, email="buyer@customer.example", *, ticket="T-1", client_id=None):
    contact = session.query(Contact).filter_by(normalized_email=email).one_or_none()
    if contact is None:
        contact = Contact(normalized_email=email, email=email, full_name="Buyer")
        session.add(contact)
        session.flush()
    conv = Conversation(contact_id=contact.id, stage="won", hubspot_ticket_id=ticket, inquiry_subject="견적 문의")
    session.add(conv)
    session.flush()
    pending = PendingWon(ticket_id=ticket, company="Customer", client_id=client_id, conversation_id=conv.id, status="pending")
    session.add(pending)
    session.flush()
    return conv, pending


def _mail(session, conv, days, body, *, inbound=True, channel="이메일", key=None):
    session.add(CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv.id, channel=channel,
                                    direction="inbound" if inbound else "outgoing", summary=body,
                                    external_id=key or f"hubspot:conv:{conv.id}-{days}", happened_at=_ago(days)))


THREAD = (
    "총 계약 금액은 USD 12,000 으로 합의했습니다. 계약은 2026-12-01 부터 시작합니다. "
    "크레딧은 64,800 이고 Stripe 로 결제합니다. 플랜은 Enterprise 입니다."
)


@pytest.fixture()
def one_ticket(factory):
    with factory() as session:
        conv, pending = _lead(session)
        _mail(session, conv, 5, "견적을 부탁드립니다.")
        _mail(session, conv, 4, THREAD, inbound=False)
        _mail(session, conv, 3, "좋습니다. 진행하겠습니다. 청구는 ap@customer.example 로 보내 주세요.")
        session.commit()
        return pending.id


def test_the_fields_come_from_the_mail_with_their_evidence(one_ticket):
    model = _FakeModel(
        amount_incl_vat=(12000, "총 계약 금액은 USD 12,000 으로 합의했습니다."),
        currency=("USD", "USD 12,000"),
        credits=("64,800", "크레딧은 64,800"),
        starts_on=("2026-12-01", "계약은 2026-12-01 부터 시작합니다"),
        payment_method=("Stripe", "Stripe 로 결제합니다"),
        plan=("Enterprise", "플랜은 Enterprise 입니다"),
        billing_email=("ap@customer.example", "청구는 ap@customer.example 로 보내 주세요"),
    )
    answer = suggest_contract(one_ticket, llm=model)

    assert answer["status"] == "ok" and answer["mails"] == 3
    values = {name: cell["value"] for name, cell in answer["fields"].items()}
    assert values == {
        "amount_incl_vat": "12000", "currency": "USD", "credits": "64800", "starts_on": "2026-12-01",
        "payment_method": "Stripe", "plan": "Enterprise", "billing_email": "ap@customer.example",
    }
    assert answer["fields"]["plan"]["evidence"] == "플랜은 Enterprise 입니다"


def test_a_value_whose_evidence_is_not_in_the_mail_stays_blank(one_ticket):
    answer = suggest_contract(one_ticket, llm=_FakeModel(
        amount_incl_vat=(99999, "총 계약 금액은 USD 99,999 입니다"),
        credits=(64800, None),
        plan=("Enterprise", "플랜은 Enterprise 입니다"),
    ))
    assert set(answer["fields"]) == {"plan"}


@pytest.mark.parametrize(
    ("name", "value", "evidence"),
    [
        ("deal_type", "연간", "플랜은 Enterprise"),
        ("currency", "EUR", "플랜은 Enterprise"),
        ("plan", "Gold", "플랜은 Enterprise"),
        ("payment_method", "현금", "플랜은 Enterprise"),
        ("payment_type", "분납", "플랜은 Enterprise"),
        ("amount_incl_vat", "약 만 달러", "총 계약 금액"),
        ("amount_incl_vat", -5, "총 계약 금액"),
        ("credits", "64800.5", "크레딧은 64,800"),
        ("installments", 500, "크레딧은 64,800"),
        ("billing_email", "메일 없음", "청구는 ap@customer.example"),
        ("starts_on", "다음 달", "계약은 2026-12-01"),
        ("starts_on", "2026-13-40", "계약은 2026-12-01"),
    ],
)
def test_a_value_of_the_wrong_shape_stays_blank(one_ticket, name, value, evidence):
    answer = suggest_contract(one_ticket, llm=_FakeModel(**{name: (value, evidence)}))
    assert answer["fields"] == {}


def test_a_start_date_needs_its_year_in_the_evidence(factory):
    """시험에서 종료일이 1년 어긋났다 — 메일에 연도가 없으면 모델이 지은 연도다."""
    with factory() as session:
        conv, pending = _lead(session)
        _mail(session, conv, 2, "12월 1일부터 시작하면 좋겠습니다.")
        session.commit()
        pending_id = pending.id
    answer = suggest_contract(pending_id, llm=_FakeModel(starts_on=("2026-12-01", "12월 1일부터 시작")))
    assert answer["fields"] == {}


def test_our_own_address_is_not_the_customers(factory):
    with factory() as session:
        conv, pending = _lead(session)
        _mail(session, conv, 2, "문의는 perso.ai@estsoft.com 으로 주세요.", inbound=False)
        session.commit()
        pending_id = pending.id
    answer = suggest_contract(pending_id, llm=_FakeModel(
        billing_email=("perso.ai@estsoft.com", "perso.ai@estsoft.com"),
        perso_email=("perso.ai@estsoft.com", "perso.ai@estsoft.com"),
        contact_info=("perso.ai@estsoft.com", "perso.ai@estsoft.com"),
    ))
    assert answer["fields"] == {}


def test_no_mail_does_not_call_the_model(factory):
    with factory() as session:
        conv, pending = _lead(session)
        _mail(session, conv, 2, "채팅 문의", channel="채팅")
        session.commit()
        pending_id = pending.id
    model = _FakeModel()
    assert suggest_contract(pending_id, llm=model) == {"status": "no_mail", "mails": 0, "fields": {}}
    assert model.calls == 0


def test_only_mail_is_read_and_reminders_are_not(factory):
    with factory() as session:
        conv, pending = _lead(session)
        _mail(session, conv, 5, "메일 본문입니다.")
        _mail(session, conv, 4, "채팅 본문입니다.", channel="채팅")
        session.add(Message(conversation_id=conv.id, direction="outgoing", subject="RE: 재촉", body="리마인더 본문입니다.",
                            status="sent", prompt_variant="followup_reminder_1", created_at=_ago(3), sent_at=_ago(3)))
        session.commit()
        pending_id = pending.id
    model = _FakeModel()
    suggest_contract(pending_id, llm=model)
    assert "메일 본문입니다." in model.thread
    assert "채팅 본문입니다." not in model.thread and "리마인더 본문입니다." not in model.thread


def test_a_first_contract_reads_the_persons_other_tickets_and_a_renewal_does_not(factory):
    """협상이 같은 사람의 다른 문의에 있을 수 있다. 재계약이면 옛 문의에는 지난 계약의 금액이 있어 안 읽는다."""
    with factory() as session:
        old, _ = _lead(session, ticket="T-old")
        _mail(session, old, 90, "옛 문의의 금액 USD 1,000")
        _new, first = _lead(session, ticket="T-new")
        _mail(session, _new, 2, "새 문의의 메일")
        session.commit()
        first_id = first.id

    model = _FakeModel()
    suggest_contract(first_id, llm=model)
    assert "옛 문의의 금액" in model.thread and "새 문의의 메일" in model.thread

    with factory() as session:
        session.add(Client(client_id=1500, company="Customer"))
        session.flush()
        session.add(ClientContract(client_id=1500, seq=1))
        pending = session.get(PendingWon, first_id)
        pending.client_id = 1500
        session.commit()

    model = _FakeModel()
    suggest_contract(first_id, llm=model)
    assert "옛 문의의 금액" not in model.thread and "새 문의의 메일" in model.thread


def test_nothing_is_written(one_ticket, factory):
    with factory() as session:
        before = (session.query(ClientContract).count(), session.query(Client).count(), session.query(PendingWon).filter_by(status="pending").count())
    suggest_contract(one_ticket, llm=_FakeModel(plan=("Enterprise", "플랜은 Enterprise 입니다")))
    with factory() as session:
        after = (session.query(ClientContract).count(), session.query(Client).count(), session.query(PendingWon).filter_by(status="pending").count())
    assert before == after


@pytest.fixture()
def console(factory):
    from fastapi.testclient import TestClient

    from src.api.main import app

    with patch("src.db.session.SessionLocal", factory), TestClient(app) as client:
        yield client


def test_the_route_answers_and_a_broken_model_still_opens_the_form(console, one_ticket):
    class Broken:
        def complete(self, *_args, **_kwargs):
            raise RuntimeError("고객 메일 내용이 든 오류")

    with patch("src.llm.client.LLMClient", lambda: _FakeModel(plan=("Enterprise", "플랜은 Enterprise 입니다"))):
        answer = console.get(f"/api/ui/won-customers/pending/{one_ticket}/suggest")
    assert answer.status_code == 200
    assert answer.json()["fields"]["plan"]["value"] == "Enterprise"

    with patch("src.llm.client.LLMClient", Broken):
        answer = console.get(f"/api/ui/won-customers/pending/{one_ticket}/suggest")
    assert answer.status_code == 200
    assert answer.json() == {"status": "failed", "mails": 0, "fields": {}}

    assert console.get("/api/ui/won-customers/pending/999999/suggest").status_code == 404
