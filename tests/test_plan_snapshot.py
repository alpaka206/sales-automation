"""티켓은 문의 시점 플랜을 들고, 리드 히스토리는 지금 값을 본다 (2026-09-07 운영자 지시).

전에는 둘이 **같은 행**(`customer_profiles`)을 읽었습니다. 그래서 고객이 나중에 플랜을
올리면 몇 달 전 문의 화면의 값까지 같이 바뀌었습니다 — 그 문의를 판단하려고 여는 화면인데,
그때 이 사람이 무엇을 쓰고 있었는지가 남지 않았습니다.

운영자 지시는 「값을 따로 관리해서 둘 다 편집은 가능하게, 한쪽이 바뀐다고 다른 쪽이 적용될
필요는 없다」입니다. 이 파일이 그 「따로」를 고정합니다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db.models import Contact, Conversation, CustomerProfile


@pytest.fixture()
def db(monkeypatch):
    """플랜이 `business` 인 고객 하나, 문의 둘 — 하나는 얼린 값이 있고 하나는 없습니다."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from src.api.routes import messages as messages_route
    from src.api.routes import ui_api
    from src.db import session as db_session
    from src.integrations import hubspot_record

    for module in (messages_route, ui_api, db_session, hubspot_record):
        monkeypatch.setattr(module, "SessionLocal", factory, raising=False)
    with factory() as session:
        contact = Contact(
            normalized_email="buyer@acme.com", email="buyer@acme.com", full_name="Acme Buyer"
        )
        session.add(contact)
        session.flush()
        session.add(CustomerProfile(contact_id=contact.id, current_plan="business",
                                    plan_tier="tier-2", user_seq="99"))
        frozen = Conversation(
            contact_id=contact.id, stage="negotiation", hubspot_ticket_id="1",
            # 문의가 들어왔을 때 이 사람은 아직 무료였습니다.
            plan_snapshot={"plan": "Free", "plan_tier": None, "user_seq": "12",
                           "space_seq": None, "plan_seq": None},
        )
        old = Conversation(contact_id=contact.id, stage="negotiation", hubspot_ticket_id="2")
        session.add_all([frozen, old])
        session.commit()
        yield factory, contact.id, frozen.id, old.id


def _rows(payload):
    plan = next(g for g in payload["groups"] if g["key"] == "plan")
    return {row["key"]: row["value"] for row in plan["rows"]}


def test_the_ticket_shows_the_plan_it_arrived_with(db):
    _factory, contact_id, frozen_id, _old = db
    with TestClient(app) as client:
        ticket = client.get(
            f"/api/ui/contacts/{contact_id}/hubspot-record?conversation_id={frozen_id}"
        ).json()
        live = client.get(f"/api/ui/contacts/{contact_id}/hubspot-record").json()

    assert _rows(ticket)["plan"] == "Free", "티켓은 문의 시점 값"
    assert _rows(live)["plan"] == "business", "리드 히스토리는 지금 값"
    # 「이 문의 시점 / 현재 값」을 적던 `frozen` 은 뺐습니다 (2026-09-07 운영자 지시).
    # 0110 뒤에 들어온 문의는 전부 얼린 값을 들고 있어 그 글자가 모든 티켓에 같은 말을
    # 하나씩 더 얹었고, 읽는 코드가 없어진 칸은 남기지 않습니다.
    assert "frozen" not in ticket


def test_a_ticket_with_no_snapshot_falls_back_to_today(db):
    """이 칸이 생기기 전의 티켓 300여 건입니다. 그때 값은 어디에도 안 남아 있어 만들어 낼
    수 없으므로 예전처럼 지금 값을 그리고, `frozen` 이 거짓이라 화면이 그렇게 적습니다."""
    _factory, contact_id, _frozen, old_id = db
    with TestClient(app) as client:
        payload = client.get(
            f"/api/ui/contacts/{contact_id}/hubspot-record?conversation_id={old_id}"
        ).json()

    assert _rows(payload)["plan"] == "business"


def test_editing_the_ticket_does_not_touch_the_live_record(db):
    """**「따로 관리」가 이 검사입니다.** 티켓 쪽 저장이 허브스팟이나 프로필로 새어 나가면
    리드 히스토리의 지금 값까지 같이 바뀌고, 그러면 따로가 아닙니다."""
    factory, contact_id, frozen_id, _old = db
    with TestClient(app) as client:
        saved = client.post(f"/tickets/{frozen_id}/plan-snapshot",
                            data={"plan": "Starter", "user_seq": "12"})
        assert saved.status_code == 200, saved.text
        live = client.get(f"/api/ui/contacts/{contact_id}/hubspot-record").json()

    assert _rows(live)["plan"] == "business", "지금 값은 그대로"
    with factory() as session:
        assert session.get(Conversation, frozen_id).plan_snapshot["plan"] == "Starter"
        assert session.get(CustomerProfile, contact_id).current_plan == "business"


def test_the_ticket_only_accepts_plan_fields(db):
    """받는 칸은 서버가 정합니다 — 화면이 보낸 이름을 그대로 담으면 이 JSON 칸이 아무
    값이나 받는 자루가 됩니다."""
    factory, _contact_id, frozen_id, _old = db
    with TestClient(app) as client:
        client.post(f"/tickets/{frozen_id}/plan-snapshot",
                    data={"plan": "Starter", "email": "attacker@evil.com"})

    with factory() as session:
        assert "email" not in session.get(Conversation, frozen_id).plan_snapshot


def test_mql_pql_follows_the_snapshot(db):
    """옆 카드와 **같은 값**을 봐야 두 칸이 같은 사실의 두 면으로 읽힙니다. 하나는 얼려
    두고 다른 하나만 최신이면 화면이 스스로 어긋나 보입니다."""
    from src.api.routes.messages import _qualification_of

    live = {"profile": {"current_plan": "business"}}
    assert _qualification_of(live) == "PQL"
    assert _qualification_of(live, {"plan": "Free"}) == "MQL"
    # `plan` 키가 아예 없을 때만 최신으로 떨어집니다 — 값이 빈 것은 「그때 플랜이 없었다」는
    # 사실이라, 그걸 메우면 얼려 둔 의미가 없습니다.
    assert _qualification_of(live, {"plan": None}) == "MQL"
    assert _qualification_of(live, {"user_seq": "1"}) == "PQL"


def test_the_stamp_waits_until_there_is_something_to_record(db):
    """처음 보는 고객은 접수 시점에 프로필이 비어 있습니다. 그때 찍으면 빈 값으로 굳어
    영원히 빈 카드가 되므로, 아는 것이 하나도 없으면 NULL 로 두고 다음 기회를 남깁니다 —
    연락처 스윕이 플랜을 받아 오는 그때가 「알게 된 첫 순간」입니다.

    그리고 **New 를 지난 티켓에는 안 찍습니다**: 답이 나간 뒤의 플랜은 그 문의를 판단할
    때의 값이 아닙니다.
    """
    from src.integrations.hubspot_record import stamp_plan_snapshot

    factory, contact_id, _frozen, _old = db
    with factory() as session:
        blank = Contact(normalized_email="new@acme.com", email="new@acme.com", full_name="New")
        session.add(blank)
        session.flush()
        fresh = Conversation(contact_id=blank.id, stage="new")
        past_new = Conversation(contact_id=contact_id, stage="negotiation")
        session.add_all([fresh, past_new])
        session.flush()

        assert stamp_plan_snapshot(session, fresh) is False, "아직 아는 것이 없습니다"
        assert fresh.plan_snapshot is None
        assert stamp_plan_snapshot(session, past_new) is False, "New 를 지났습니다"

        session.add(CustomerProfile(contact_id=blank.id, current_plan="Free"))
        session.flush()
        assert stamp_plan_snapshot(session, fresh) is True
        assert fresh.plan_snapshot["plan"] == "Free"
        # 한 번 찍힌 값은 다시 안 건드립니다 — 티켓 하나에 이벤트가 여러 번 옵니다.
        assert stamp_plan_snapshot(session, fresh) is False


def test_the_ticket_card_saves_to_both_places_in_one_press():
    """티켓 화면은 티켓 정보·플랜·연락처가 **한 상자**입니다 (2026-09-07 운영자 지시).

    상자가 하나면 연필도 저장도 하나여야 하는데, 값이 사는 곳은 둘입니다 — 플랜은 이
    티켓, 회사·메모는 이 사람. 한쪽만 부르면 그 카드에서 고친 값의 절반이 조용히 사라지고,
    화면은 저장했다고 말합니다.

    **플랜이 먼저입니다**: 뒤엣것이 실패해도 앞엣것은 남고, 플랜은 이 화면에서만 고칠 수
    있는 반면 회사·메모는 고객 상세에도 자리가 있습니다.
    """
    import pathlib

    screen = pathlib.Path("frontend/src/screens/MessageDetail.tsx").read_text(encoding="utf-8")
    save = screen[screen.index("async function saveTicketCard"):]
    save = save[: save.index("\n  }\n")]
    assert save.index("/plan-snapshot") < save.index("/edit")
