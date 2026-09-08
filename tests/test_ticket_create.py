"""티켓을 손으로 만든다 (2026-09-08 운영자 지시).

전화로 받은 문의, 행사장 명함, 영업이 먼저 연락한 건 — 허브스팟 폼도 메일도 안 지나서
이 콘솔에 행이 안 생기던 것들입니다. 보드 New 열의 `+` 가 이 길을 엽니다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.common.config import settings
from src.db.base import Base
from src.db.models import Contact, Conversation


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from src.api.routes import customer_ops, ui_api
    from src.db import session as db_session

    for module in (customer_ops, ui_api, db_session):
        monkeypatch.setattr(module, "SessionLocal", factory, raising=False)
    monkeypatch.setattr(settings, "HUBSPOT_TICKET_STAGE_NEW", "stage-new")
    return factory


class _Client:
    def __init__(self):
        self.made = []

    def find_or_create_contact_sync(self, email, *, full_name="", company=""):
        self.made.append(("contact", email, full_name, company))
        return "hs-contact-1"

    def create_ticket_sync(self, *, subject, content, stage_id, contact_id):
        self.made.append(("ticket", subject, stage_id, contact_id))
        return "T-NEW"


def _post(client, **data):
    payload = {"email": "buyer@acme.com", "full_name": "Acme Buyer",
               "subject": "전화 문의 — 더빙 단가"}
    payload.update(data)
    return client.post("/pipeline/tickets", data=payload, follow_redirects=False)


def test_a_ticket_is_made_in_hubspot_first_then_here(db):
    """**허브스팟에 먼저 만듭니다.** 우리 쪽에만 만들면 그 문의는 티켓 번호가 없어서
    회신을 보낼 스레드도, 단계를 미러링할 곳도 없습니다 — 화면에는 카드가 서는데
    아무것도 못 하는 상태입니다."""
    fake = _Client()
    with TestClient(app) as client, patch(
        "src.integrations.hubspot.HubSpotClient", lambda: fake
    ):
        response = _post(client, company="Acme")

    assert response.status_code == 303
    assert ("ticket", "전화 문의 — 더빙 단가", "stage-new", "hs-contact-1") in fake.made
    with db() as session:
        conversation = session.query(Conversation).one()
        contact = session.get(Contact, conversation.contact_id)
        assert conversation.hubspot_ticket_id == "T-NEW"
        assert conversation.stage == "new"
        assert contact.hubspot_contact_id == "hs-contact-1"
        assert contact.company == "Acme"
        # **워크북으로 실려 나가면 안 됩니다.** 이 칸은 append 대기열의 방아쇠라,
        # 채우면 손으로 만든 티켓이 영업팀 공용 시트에 바로 올라갑니다.
        assert conversation.last_incoming_at is None


def test_the_required_three_are_required(db):
    """이메일 · 이름 · 제목뿐입니다. 나머지를 필수로 만들면 「지금 아는 것만 적고 나중에
    채운다」가 안 됩니다 — 전화를 받는 중에 쓰는 폼입니다."""
    fake = _Client()
    with TestClient(app) as client, patch(
        "src.integrations.hubspot.HubSpotClient", lambda: fake
    ):
        assert _post(client, email="notanaddress").status_code == 400
        assert _post(client, full_name="  ").status_code == 400
        assert _post(client, subject="").status_code == 400
        # 회사와 내용이 비어도 만들어집니다.
        assert _post(client, company="", content="").status_code == 303
    assert len([m for m in fake.made if m[0] == "ticket"]) == 1


def test_an_existing_contact_is_reused_not_duplicated(db):
    """같은 사람이 둘로 갈리면 그 뒤로 티켓·메일·플랜이 두 갈래로 쌓이고, 합치는 것은
    사람이 할 일이 됩니다.

    그리고 **있던 값은 안 덮어씁니다** — 이 폼은 티켓을 만드는 자리이지 연락처를 고치는
    자리가 아닙니다. 여기서 덮으면 고객 상세에서 채운 회사 이름이 티켓 하나 만들 때마다
    지워집니다.
    """
    with db() as session:
        session.add(Contact(normalized_email="buyer@acme.com", email="buyer@acme.com",
                            full_name="원래 이름", company="원래 회사"))
        session.commit()

    fake = _Client()
    with TestClient(app) as client, patch(
        "src.integrations.hubspot.HubSpotClient", lambda: fake
    ):
        _post(client, full_name="다르게 적은 이름", company="다르게 적은 회사")

    with db() as session:
        contact = session.query(Contact).one()
        assert contact.full_name == "원래 이름"
        assert contact.company == "원래 회사"
        assert session.query(Conversation).count() == 1


def test_the_form_can_fill_itself_from_what_we_already_know(db):
    """「기존에 리드 히스토리에 있을 수도 있으니 정보 불러올 수도 있도록」(운영자).

    **없으면 빈 답입니다** — 404 가 아닙니다. 처음 보는 주소는 오류가 아니라 흔한
    경우고, 화면은 그때 빈 칸을 그대로 둡니다.
    """
    with db() as session:
        contact = Contact(normalized_email="buyer@acme.com", email="buyer@acme.com",
                          full_name="Acme Buyer", company="Acme")
        session.add(contact)
        session.flush()
        session.add(Conversation(contact_id=contact.id, stage="new"))
        session.commit()

    with TestClient(app) as client:
        found = client.get("/api/ui/contacts/lookup?email=Buyer@Acme.com").json()
        missing = client.get("/api/ui/contacts/lookup?email=nobody@acme.com").json()
        invalid = client.get("/api/ui/contacts/lookup?email=nope").json()

    assert found == {"found": True, "id": found["id"], "full_name": "Acme Buyer",
                     "company": "Acme", "tickets": 1}
    assert missing == {"found": False}
    assert invalid == {"found": False}


def test_an_existing_customer_can_be_found_by_any_fragment(db):
    """**기존 고객 불러오기** (2026-09-08 운영자 지시) — 이름·회사·이메일 아무 조각으로나.

    전화를 받는 중에는 상대 주소를 정확히 모르고 회사 이름만 기억날 때가 흔합니다.
    주소로만 찾게 하면 그때 새 연락처를 만들게 되고, 같은 사람이 둘로 갈립니다.

    **두 글자부터** 찾습니다 — 한 글자로는 거의 모든 행이 걸려서 고르개가 목록이 됩니다.
    """
    with db() as session:
        session.add_all([
            Contact(normalized_email="buyer@acme.com", email="buyer@acme.com",
                    full_name="Acme Buyer", company="Acme Corp"),
            Contact(normalized_email="other@zeta.com", email="other@zeta.com",
                    full_name="Zeta Person", company="Zeta"),
        ])
        session.commit()

    with TestClient(app) as client:
        by_company = client.get("/api/ui/contacts/search?q=acme%20co").json()["rows"]
        by_name = client.get("/api/ui/contacts/search?q=zeta%20per").json()["rows"]
        by_email = client.get("/api/ui/contacts/search?q=buyer@").json()["rows"]
        too_short = client.get("/api/ui/contacts/search?q=a").json()["rows"]

    assert [r["email"] for r in by_company] == ["buyer@acme.com"]
    assert [r["email"] for r in by_name] == ["other@zeta.com"]
    assert [r["email"] for r in by_email] == ["buyer@acme.com"]
    assert too_short == [], "한 글자로는 안 찾습니다"


def test_the_customer_screen_makes_a_ticket_with_the_same_form():
    """고객 상세의 「티켓 생성」은 보드 `+` 와 **같은 폼**입니다 — 다른 점은 누구의
    티켓인지가 이미 정해져 있다는 것뿐입니다.

    폼이 두 벌이면 필수 칸이 한쪽만 늘어나고, 그 어긋남은 둘을 나란히 놓기 전에는 안
    보입니다. 그리고 그 화면에서 다른 사람을 고를 수 있게 두면 「이 고객의 티켓을
    만든다」가 아니게 됩니다.
    """
    import pathlib

    screen = pathlib.Path("frontend/src/screens/CustomerDetail.tsx").read_text(encoding="utf-8")
    board = pathlib.Path("frontend/src/ui/Board.tsx").read_text(encoding="utf-8")
    for source in (screen, board):
        assert "NewTicketForm" in source

    form = pathlib.Path("frontend/src/ui/NewTicketForm.tsx").read_text(encoding="utf-8")
    # 고객이 정해져 있으면 찾기를 안 그리고 이메일을 못 고칩니다.
    assert "{!contact && (" in form
    assert "readOnly={!!contact}" in form
