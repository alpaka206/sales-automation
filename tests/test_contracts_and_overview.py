"""수주 고객 and 전체 대시보드 — the two screens that were "준비 중" placeholders.

Neither invents data. 수주 고객 is the contract book; 전체 대시보드 is a roll-up whose
every number belongs to a screen further in. So what is worth pinning is not the layout
but the arithmetic that could quietly lie: money summed across currencies, an expiry that
was never recorded reading as "expires today", and the roll-up disagreeing with the
screen it summarises.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes.customer_ops import (
    CONTRACT_STATUS_LABELS,
    CONTRACT_STATUSES,
    RENEWAL_WINDOW_DAYS,
    _contract_rows,
    _contract_summary,
)
from src.db.models import Contact, ContractRecord
from src.db.session import SessionLocal


@pytest.fixture
def book():
    """Four contracts: two live in different currencies, one lapsed, one with no expiry.

    Cleans up after itself: the suite shares one sqlite file, so rows left behind would
    change the totals the next test asserts.
    """
    now = datetime.utcnow()
    made = []
    with SessionLocal() as session:
        for index, (status, amount, currency, expires) in enumerate(
            [
                ("active", Decimal("6000000"), "KRW", now + timedelta(days=10)),
                ("active", Decimal("1000"), "USD", now + timedelta(days=400)),
                ("expired", Decimal("500000"), "KRW", now - timedelta(days=5)),
                ("draft", None, "KRW", None),
            ]
        ):
            email = f"book{index}@example.com"
            contact = Contact(email=email, normalized_email=email, full_name=f"고객 {index}",
                              company=f"회사 {index}", domain="example.com")
            session.add(contact)
            session.flush()
            contract = ContractRecord(
                contact_id=contact.id, status=status, amount=amount,
                currency=currency, expires_at=expires, contract_date=now - timedelta(days=30),
            )
            session.add(contract)
            made.append((contact, contract))
        session.commit()
        ids = [(contact.id, contract.id) for contact, contract in made]

    yield ids

    with SessionLocal() as session:
        for contact_id, _contract_id in ids:
            # The contract goes with it — contact_id is ON DELETE CASCADE.
            session.delete(session.get(Contact, contact_id))
        session.commit()


def test_money_is_summed_per_currency_never_across_them(book):
    """₩6,000,000 + $1,000 is not 6,001,000 of anything. The workbook holds both, and one
    number covering both currencies is worse than showing no number at all."""
    summary = _contract_summary()
    amounts = {entry["currency"]: entry["amount"] for entry in summary["active_amounts"]}
    assert amounts["KRW"] == Decimal("6000000")
    assert amounts["USD"] == Decimal("1000")


def test_only_live_contracts_count_towards_the_money(book):
    """An expired contract is history, not revenue. A draft was never signed."""
    summary = _contract_summary()
    total = sum(entry["amount"] for entry in summary["active_amounts"])
    assert total == Decimal("6001000")  # the two active ones, per currency, nothing else


def test_a_contract_with_no_expiry_is_not_a_contract_expiring_today(book):
    """None means nobody recorded an end date. Rendering that as 0 days would put it at
    the top of the renewal list and push a real renewal down."""
    rows = {row["id"]: row for row in _contract_rows()}
    no_expiry = [row for row in rows.values() if row["expires_at"] is None]
    assert no_expiry and all(row["days_to_expiry"] is None for row in no_expiry)


def test_a_lapsed_contract_reports_negative_days_not_zero(book):
    lapsed = [row for row in _contract_rows() if row["status"] == "expired"]
    assert lapsed and lapsed[0]["days_to_expiry"] < 0


def test_the_renewal_window_is_one_number_for_both_screens(book):
    """수주 고객 and 전체 대시보드 both say "N일 내 만료". Two windows is how a renewal is
    missed on whichever screen used the longer one."""
    summary = _contract_summary()
    assert summary["renewal_window_days"] == RENEWAL_WINDOW_DAYS
    # 10 days out is inside the window; 400 days is not.
    assert summary["expiring_soon"] == 1


def test_the_status_vocabulary_is_the_one_the_write_route_accepts():
    """A filter chip for a status no contract can hold is a chip that always reads 0."""
    assert {key for key, _label in CONTRACT_STATUS_LABELS} == CONTRACT_STATUSES


def test_filtering_and_search_narrow_the_same_rows(book):
    assert all(row["status"] == "active" for row in _contract_rows(status="active"))
    assert [row["company"] for row in _contract_rows(query="회사 1")] == ["회사 1"]
    assert _contract_rows(query="존재하지 않는 회사") == []


def test_the_contract_book_is_served(book):
    """전체 대시보드(`/api/ui/overview`)는 지웠습니다 — 각 화면의 숫자를 모아 보여 주기만
    하는 자리라 아무도 안 봤습니다(운영자 지시). 남은 것은 계약 장부 하나입니다."""
    with TestClient(app) as client:
        contracts = client.get("/api/ui/contracts")
        gone = client.get("/api/ui/overview")
    assert contracts.status_code == 200
    assert len(contracts.json()["rows"]) >= 4
    assert gone.status_code == 404

    from src.api.routes import dashboard

    assert not hasattr(dashboard, "_overview_context")
