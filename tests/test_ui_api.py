"""The JSON the React screens read, and the live-update stream.

Two rules this file exists to hold. First, ``/api/ui`` is a second door onto the same
screens — never a way around what guards them. Second, a change made on one screen has to
reach the others, which React state cannot do on its own: only the server knows.
"""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes import ui_api


def test_the_json_screens_are_browser_paths_not_token_api_routes():
    """They carry the operator's session cookie like every other screen. Left out of
    WEB_UI_PREFIXES the auth middleware reads them as JSON API routes and demands an
    internal token, and the whole console answers 401."""
    from src.api.security import is_web_ui_path

    assert is_web_ui_path("/api/ui/dashboard")
    assert is_web_ui_path("/app")
    assert is_web_ui_path("/app/messages/12")
    # …and a real API route still is one.
    assert not is_web_ui_path("/api/inbound/run")


def test_the_log_json_answers_exactly_as_its_screen_does():
    """Same gate, same answer — asserted against the page rather than a fixed code.

    A JSON copy of a screen must not be a way around that screen's gate, and must not
    refuse what the screen allows. Pinning the codes together is what catches either
    drift; this app has two different admin checks, so picking the wrong one is easy.
    """
    with TestClient(app) as client:
        assert client.get("/api/ui/logs").status_code == client.get("/logs").status_code


def test_an_unknown_message_is_a_404_not_a_blank_screen():
    with TestClient(app) as client:
        assert client.get("/api/ui/messages/999999").status_code == 404
        assert client.get("/api/ui/customers/999999").status_code == 404
        assert client.get("/api/ui/pipeline/nonsense/cards").status_code == 404


def test_every_screen_the_sidebar_offers_has_working_json():
    """The cutover's other half: a screen with no data endpoint is a blank page. Each of
    these 500'd or 404'd would be a menu entry that opens nothing."""
    with TestClient(app) as client:
        for path in (
            "/api/ui/dashboard",
            "/api/ui/messages",
            "/api/ui/customers",
            "/api/ui/companies/acme.com",
            "/api/ui/email-templates",
            "/api/ui/policy-docs",
            "/api/ui/logs",
            "/api/ui/recovery",
        ):
            assert client.get(path).status_code == 200, path


def test_the_document_screens_render_with_a_row_in_them():
    """**빈 표로는 이 두 화면을 못 잽니다.** 위 테스트는 모든 화면을 훑지만 DB 가 비어
    있어서, 행 하나하나를 그리는 목록 컴프리헨션이 **한 번도 안 돕니다.** 그래서 없어진
    칸을 아직 읽던 줄이 두 번 통과했고 두 번 다 운영에서 500 이었습니다 (2026-08-27:
    `item["deleted"]`, `row.author`).

    행을 하나씩 넣고 부릅니다 — 그래야 그 줄들이 실제로 실행됩니다.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from src.db.base import Base
    from src.db.models import EmailTemplate, PolicySource

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([
            EmailTemplate(key="reply_format", name="답변 메일 형식", language="ko", version=2, body="뼈대", author="배운태"),
            EmailTemplate(key="signature_x", name="서명", language="all", version=1, body="<p/>"),
            PolicySource(label="지원 언어 정책", title="지원 언어 정책", doc_key="k" * 32,
                         mode="knowledge", version=3, body="99개 언어",
                         usage_note="언어 문의에 씁니다"),
            PolicySource(label="공통 원칙", title="공통 원칙", doc_key="r" * 32,
                         mode="rules", version=1, body="규칙"),
        ])
        session.commit()

    with patch("src.db.session.SessionLocal", factory), TestClient(app) as client:
        templates = client.get("/api/ui/email-templates")
        docs = client.get("/api/ui/policy-docs")

    assert templates.status_code == 200, templates.text
    assert docs.status_code == 200, docs.text
    items = {item["key"]: item for item in templates.json()["items"]}
    assert items["reply_format"]["version"] == 2
    assert items["reply_format"]["author"] == "배운태"
    counts = {kind["key"]: kind["count"] for kind in templates.json()["kinds"]}
    assert counts["signature"] == 1 and counts["template"] == 1
    rows = {row["label"]: row for row in docs.json()["rows"]}
    assert rows["지원 언어 정책"]["version"] == 3
    assert rows["지원 언어 정책"]["updated_at"]


def test_a_personal_domain_is_never_grouped_as_one_company():
    """gmail/naver addresses share a domain but not a customer — grouping them would
    show one customer's conversations to an unrelated one."""
    with TestClient(app) as client:
        payload = client.get("/api/ui/companies/gmail.com").json()
    assert payload["personal_domain"] is True
    assert payload["conversations"] == []


# ---- 보드 카드의 리마인더 칩 (2026-09-22 운영자 지시: 「리마인더 센트 기본적으로 떠있게」) ----


def test_a_contacted_card_carries_the_reminder_chip_and_a_new_card_does_not(monkeypatch):
    """보드와 티켓 배너는 **같은 함수**(`followup_sequence.view`)를 읽습니다 — 둘이 다른 말을
    하면 운영자가 어느 쪽을 믿을지 화면만 봐서는 모릅니다. 시퀀스가 도는 Contacted 카드는
    한 통도 안 나갔어도 「Pending」이고, 시퀀스 밖의 카드(New)는 칩이 없습니다(None).
    첫 그림(`/api/ui/dashboard`)과 「더 보기」(`/api/ui/pipeline/…/cards`)가 같은 카드를
    다르게 그리면 안 되므로 둘 다 잽니다."""
    from datetime import datetime, timedelta, timezone

    from src.common.config import settings
    from src.db.models import Contact, Conversation, Message
    from src.db.session import SessionLocal

    monkeypatch.setattr(settings, "FOLLOWUP_SEQUENCE_SINCE", "2026-01-01")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        contact = Contact(normalized_email="chip@example.com", email="chip@example.com", full_name="Chip")
        session.add(contact)
        session.flush()
        contacted = Conversation(contact_id=contact.id, stage="meeting_link_sent",
                                 hubspot_ticket_id="T-chip-1", inquiry_subject="quote")
        fresh = Conversation(contact_id=contact.id, stage="new",
                             hubspot_ticket_id="T-chip-2", inquiry_subject="hello")
        session.add_all([contacted, fresh])
        session.flush()
        session.add(Message(conversation_id=contacted.id, direction="outgoing", status="sent",
                            body="our answer", sent_at=now - timedelta(days=1)))
        session.commit()
        contacted_id, fresh_id, contact_id = contacted.id, fresh.id, contact.id

    try:
        with TestClient(app) as client:
            board = client.get("/api/ui/dashboard").json()
            page = client.get("/api/ui/pipeline/meeting_link_sent/cards").json()
        cards = {c["conversation_id"]: c for stage in board["stages"] for c in stage["cards"]}
        assert cards[contacted_id]["reminder"] == "Pending"
        assert cards[fresh_id]["reminder"] is None
        assert {c["conversation_id"]: c["reminder"] for c in page["cards"]}[contacted_id] == "Pending"
    finally:
        # 공용 임시 DB 다 — 다음 테스트가 이 카드를 보게 두지 않는다.
        with SessionLocal() as session:
            session.query(Message).filter(Message.conversation_id.in_([contacted_id, fresh_id])).delete()
            session.query(Conversation).filter(Conversation.id.in_([contacted_id, fresh_id])).delete()
            session.query(Contact).filter(Contact.id == contact_id).delete()
            session.commit()



# ---- live updates ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_write_reaches_every_open_console():
    """publish() is what a stage move calls; each subscriber is one open tab."""
    first: asyncio.Queue[str] = asyncio.Queue()
    second: asyncio.Queue[str] = asyncio.Queue()
    ui_api._subscribers.update({first, second})
    try:
        ui_api.publish("pipeline")
        assert first.get_nowait() == "pipeline"
        assert second.get_nowait() == "pipeline"
    finally:
        ui_api._subscribers.difference_update({first, second})


@pytest.mark.asyncio
async def test_a_stalled_tab_cannot_block_a_write():
    """A browser that stopped reading fills its queue. Dropping it is right; blocking the
    operator's save because a dead tab is full is not."""
    full: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    full.put_nowait("earlier")
    ui_api._subscribers.add(full)
    try:
        ui_api.publish("pipeline")  # must not raise, must not hang
        assert full not in ui_api._subscribers
    finally:
        ui_api._subscribers.discard(full)


def test_broadcasting_never_breaks_the_write_it_follows(monkeypatch):
    """Fire-and-forget: the save already happened. A broadcast that explodes must not
    turn a successful write into a 500 the operator retries."""
    from fastapi.testclient import TestClient

    from src.api.main import app

    def boom(_topic: str) -> None:
        raise RuntimeError("no listeners")

    monkeypatch.setattr(ui_api, "publish", boom)
    with TestClient(app) as client:
        # Any write route; this one needs no fixtures and no external call.
        response = client.post("/logs/clear", follow_redirects=False)
    assert response.status_code < 500


def test_every_write_route_reaches_the_other_tabs(monkeypatch):
    """The point of the middleware: no handler has to remember.

    Three of thirty endpoints used to announce by hand — a stage move and a logged
    interaction. Sending a reply, approving access, editing a template, adding a
    contract: none of them reached another tab.
    """
    from fastapi.testclient import TestClient

    from src.api.main import app

    seen: list[str] = []
    monkeypatch.setattr(ui_api, "publish", seen.append)
    with TestClient(app) as client:
        client.post("/logs/clear", follow_redirects=False)
        client.get("/healthz")
    assert seen == ["/logs/clear"], "a successful write must publish, a read must not"
