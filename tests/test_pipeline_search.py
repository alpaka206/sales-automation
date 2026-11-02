"""문의 파이프라인 위 돋보기 — 이름 · 이메일 일부로 모든 열에서 티켓을 찾는다 (2026-11-02 운영자).

보드는 열마다 15장만 그리므로 찾기는 서버가 한다. 화면에서 거르면 그 뒤의 티켓은 있어도 「없음」이 된다.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.api.routes import customer_ops
from src.db.base import Base
from src.db.models import Contact, Conversation


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def console(factory):
    from fastapi.testclient import TestClient

    from src.api.main import app

    with patch.object(customer_ops, "SessionLocal", factory), \
         patch("src.db.session.SessionLocal", factory), TestClient(app) as client:
        yield client


def _ticket(session, email: str, name: str, stage: str = "new", *, company: str | None = None, age: int = 0) -> int:
    contact = session.query(Contact).filter_by(normalized_email=email).one_or_none()
    if contact is None:
        contact = Contact(normalized_email=email, email=email, full_name=name, company=company)
        session.add(contact)
        session.flush()
    conv = Conversation(contact_id=contact.id, stage=stage, hubspot_ticket_id=f"T-{email}-{stage}-{age}",
                        inquiry_subject=f"문의 {email}",
                        created_at=(datetime.now(timezone.utc) - timedelta(days=age)).replace(tzinfo=None))
    session.add(conv)
    session.flush()
    return conv.id


def _search(console, q: str) -> dict:
    answer = console.get("/api/ui/pipeline/search", params={"q": q})
    assert answer.status_code == 200, answer.text
    return answer.json()


def _found_emails(result: dict) -> set[str]:
    return {card["email"] for stage in result["stages"] for card in stage["cards"]}


def test_an_email_or_a_name_finds_the_ticket_whatever_the_case(console, factory):
    with factory() as session:
        _ticket(session, "alice@acme.example", "Alice Kim")
        _ticket(session, "bob@other.example", "홍길동", "negotiation")
        session.commit()

    assert _found_emails(_search(console, "ACME.example")) == {"alice@acme.example"}
    assert _found_emails(_search(console, "alice k")) == {"alice@acme.example"}
    assert _found_emails(_search(console, "길동")) == {"bob@other.example"}
    assert _found_emails(_search(console, "  길동  ")) == {"bob@other.example"}


def test_only_name_and_email_are_searched(console, factory):
    with factory() as session:
        _ticket(session, "carol@example.com", "Carol", company="UniqueCompany")
        session.commit()
    assert _search(console, "UniqueCompany")["total"] == 0


def test_every_column_is_searched_and_each_counts_what_it_found(console, factory):
    with factory() as session:
        _ticket(session, "dana@example.com", "Dana", "new")
        _ticket(session, "dana@example.com", "Dana", "won", age=3)
        _ticket(session, "dana@example.com", "Dana", "closed", age=5)
        _ticket(session, "someone@else.example", "Someone", "won")
        session.commit()

    result = _search(console, "dana@")
    assert result["total"] == 3
    totals = {stage["key"]: stage["total"] for stage in result["stages"]}
    assert (totals["new"], totals["won"], totals["closed"], totals["negotiation"]) == (1, 1, 1, 0)
    # 머리글과 카드가 같은 말을 한다 — 빈 열도 모양은 그대로 온다(그리지 않는 것은 화면의 일이다).
    assert all(stage["total"] == len(stage["cards"]) for stage in result["stages"])


def test_a_ticket_past_the_fifteen_the_board_draws_is_still_found(console, factory):
    """이 기능의 이유다 — 화면에서 걸렀으면 이 티켓은 「없음」이다."""
    with factory() as session:
        _ticket(session, "buried@example.com", "Buried", "new", age=30)
        for index in range(20):
            _ticket(session, f"recent{index}@example.org", f"Recent {index}", "new", age=index)
        session.commit()

    board = console.get("/api/ui/dashboard").json()
    new_column = next(stage for stage in board["stages"] if stage["key"] == "new")
    assert "buried@example.com" not in {card["email"] for card in new_column["cards"]}

    assert _found_emails(_search(console, "buried")) == {"buried@example.com"}


def test_more_cards_stay_inside_the_search(console, factory):
    with factory() as session:
        for index in range(20):
            _ticket(session, f"many{index}@wanted.example", f"Many {index}", "new", age=index)
        for index in range(5):
            _ticket(session, f"other{index}@else.example", f"Other {index}", "new", age=100 + index)
        session.commit()

    result = _search(console, "wanted.example")
    column = next(stage for stage in result["stages"] if stage["key"] == "new")
    assert (column["total"], len(column["cards"])) == (20, 15)

    page = console.get("/api/ui/pipeline/new/cards", params={"offset": 15, "q": "wanted.example"}).json()
    assert len(page["cards"]) == 5 and page["has_more"] is False
    assert all(card["email"].endswith("@wanted.example") for card in page["cards"])

    # 돋보기 없는 「더 보기」는 그대로다.
    plain = console.get("/api/ui/pipeline/new/cards", params={"offset": 15}).json()
    assert len(plain["cards"]) == 10


def test_underscore_and_percent_are_plain_letters(console, factory):
    with factory() as session:
        _ticket(session, "first_last@example.com", "First Last")
        _ticket(session, "firstxlast@example.com", "Firstx Last")
        session.commit()

    assert _found_emails(_search(console, "first_last")) == {"first_last@example.com"}
    assert _search(console, "%")["total"] == 0


def test_nothing_found_and_nothing_asked_answer_empty(console, factory):
    with factory() as session:
        _ticket(session, "erin@example.com", "Erin")
        session.commit()

    for q in ("no-such-person", "", "   "):
        result = _search(console, q)
        assert result["total"] == 0
        assert all(stage["cards"] == [] for stage in result["stages"])
    assert _search(console, "x" * 5000)["total"] == 0


def test_the_result_is_refreshed_with_the_board():
    """단계를 옮긴 뒤의 무효화가 `["dashboard"]` 앞머리로 간다 — 결과의 질의 키가 거기 안 들면 옮긴 카드가 옛 열에 남는다."""
    screen = pathlib.Path("frontend/src/screens/Dashboard.tsx").read_text(encoding="utf-8")
    assert 'queryKey: ["dashboard", "search", query]' in screen
    board = pathlib.Path("frontend/src/ui/Board.tsx").read_text(encoding="utf-8")
    assert 'invalidateQueries({ queryKey: ["dashboard"] })' in board
