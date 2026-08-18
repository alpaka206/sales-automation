"""소통 히스토리 — the manual touchpoint log, and the two new doors into it.

Up to 답변 발송 this app owns the thread: it acknowledges, drafts, and sends the reply
through HubSpot. Everything after that happens where the customer prefers — email,
WhatsApp, phone, SMS — and never reaches HubSpot at all, so the operator types it in.
Three surfaces offer the same form (리드 히스토리, 티켓 세부 내역, and the board's + on a
card), and what they produce has to end up on the right ticket.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.api.routes.customer_ops import MANUAL_LOG_STAGES, PIPELINE_STAGES
from src.db.base import Base
from src.db.models import Contact, Conversation, CustomerInteraction, Message


@pytest.fixture()
def log_db():
    """One customer with two inquiries, each holding one message.

    Both route modules are patched: the board and the ticket screen read through
    customer_ops / messages respectively, and a half-patched pair silently reads the
    real database.
    """
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        contact = Contact(
            normalized_email="buyer@acme.com",
            email="buyer@acme.com",
            full_name="Acme Buyer",
            company="Acme",
            domain="acme.com",
            sheet_client_id=1330,
        )
        session.add(contact)
        session.flush()
        ids = {"contact": contact.id}
        for key, stage, subject in (
            ("won", "won", "작년 계약 건"),
            ("negotiating", "negotiation", "더빙 단가 문의"),
        ):
            conv = Conversation(
                contact_id=contact.id,
                stage=stage,
                inquiry_subject=subject,
                created_at=datetime.now() - timedelta(days=1 if key == "won" else 0),
            )
            session.add(conv)
            session.flush()
            message = Message(
                conversation_id=conv.id,
                direction="outgoing",
                channel="email",
                subject=f"RE: {subject}",
                body="draft",
                status="pending_approval",
            )
            session.add(message)
            session.flush()
            ids[key] = conv.id
            ids[f"{key}_message"] = message.id
        session.commit()
    with (
        patch("src.api.routes.customer_ops.SessionLocal", factory),
        patch("src.api.routes.messages.SessionLocal", factory),
        patch("src.api.routes.dashboard.SessionLocal", factory),
    ):
        yield factory, ids


def _post(client, contact_id, **data):
    payload = {"channel": "email", "summary": "기록"}
    payload.update(data)
    return client.post(
        f"/customers/{contact_id}/interactions", data=payload, follow_redirects=False
    )


# ---- one record is the whole exchange -----------------------------------------------


def test_the_form_asks_who_handled_it_not_which_way_it_went():
    """A record is the back-and-forth written up once, so "고객 → 우리" has no single
    answer for it. Who was on the call does, and months later nothing else can say."""
    import pathlib

    form = pathlib.Path("frontend/src/ui/InteractionForm.tsx").read_text(
        encoding="utf-8"
    )
    assert 'name="handler"' in form
    assert 'name="direction"' not in form


def test_a_record_keeps_the_handler_and_no_direction(log_db):
    factory, ids = log_db
    with TestClient(app) as client:
        _post(
            client,
            ids["contact"],
            channel="phone",
            handler="박세일",
            summary="전화로 단가 재확인, 분당 $1.8 검토 후 회신하기로 함.",
            conversation_id=str(ids["negotiating"]),
        )
    with factory() as session:
        record = session.query(CustomerInteraction).one()
        assert record.handler == "박세일"
        # The column stays for HubSpot-synced rows; a hand-written one has no direction.
        assert record.direction == "note"


def test_the_handler_shows_on_the_ticket_and_note_is_not_labelled(log_db):
    factory, ids = log_db
    with factory() as session:
        session.add(
            CustomerInteraction(
                contact_id=ids["contact"],
                conversation_id=ids["negotiating"],
                channel="whatsapp",
                direction="note",
                handler="박세일",
                summary="왓츠앱으로 견적 재확인",
            )
        )
        session.commit()
    with TestClient(app) as client:
        record = client.get(f"/api/ui/messages/{ids['negotiating_message']}").json()
    logged = record["ticket_interactions"][0]
    assert logged["handler"] == "박세일"
    # 'note' means "no direction". The screen drops that tag rather than labelling every
    # row with a word that says nothing — see InteractionForm.tsx.
    assert logged["direction"] == "note"


# ---------- the board reads a page, not the whole database ----------


def test_a_column_loads_one_page_and_still_counts_them_all(log_db):
    """The board used to read EVERY conversation, contact and profile on every dashboard
    request, to draw columns nobody scrolls to the bottom of. It reads a page now — but
    the header count must stay the real total, or capping quietly changes the number an
    operator reads off the board."""
    from src.api.routes.customer_ops import _pipeline_rows

    factory, ids = log_db
    with factory() as session:
        for index in range(5):
            session.add(
                Conversation(
                    contact_id=ids["contact"],
                    stage="negotiation",
                    inquiry_subject=f"추가 문의 {index}",
                )
            )
        session.commit()

    rows, totals = _pipeline_rows(limit=2)
    assert len([row for row in rows if row["stage"] == "negotiation"]) == 2  # one page
    assert totals["negotiation"] == 6                                       # …of six
    # Every column is capped by the same single query, not one query per column.
    assert len([row for row in rows if row["stage"] == "won"]) == 1


def test_a_column_page_picks_up_where_the_last_one_stopped(log_db):
    from src.api.routes.customer_ops import _pipeline_rows

    factory, ids = log_db
    with factory() as session:
        for index in range(4):
            session.add(
                Conversation(
                    contact_id=ids["contact"], stage="won", inquiry_subject=f"수주 {index}"
                )
            )
        session.commit()

    first, totals = _pipeline_rows(stage="won", limit=2, offset=0)
    second, _ = _pipeline_rows(stage="won", limit=2, offset=2)
    assert totals["won"] == 5
    assert len(first) == 2 and len(second) == 2
    # No row may appear on two pages — the column would render it twice.
    assert not {row["conversation"].id for row in first} & {
        row["conversation"].id for row in second
    }


def test_the_column_endpoint_stops_asking_when_the_column_runs_out(log_db):
    """``has_more`` false is what ends the chain — the board stops asking for pages."""
    with TestClient(app) as client:
        more = client.get("/api/ui/pipeline/negotiation/cards?offset=0").json()
        done = client.get("/api/ui/pipeline/negotiation/cards?offset=99").json()
        bad = client.get("/api/ui/pipeline/nonsense/cards")
    assert len(more["cards"]) == 1           # only one negotiating thread exists
    assert more["has_more"] is False
    assert done["cards"] == []
    assert bad.status_code == 404


# ---------- which stages offer the + button ----------


def test_manual_logging_starts_where_the_automated_reply_ends():
    """새 문의 has not been answered yet — there is nothing manual to log there."""
    assert "new" not in MANUAL_LOG_STAGES
    assert MANUAL_LOG_STAGES[0] == "meeting_link_sent"
    # Every later stage, in board order, and nothing invented.
    assert list(MANUAL_LOG_STAGES) == [key for key, _, _ in PIPELINE_STAGES if key != "new"]


def test_the_board_shows_the_button_only_on_those_stages(log_db):
    import re

    with TestClient(app) as client:
        html = client.get("/").text
    for match in re.finditer(
        r'<section class="kanban-column"[^>]*data-stage="([a-z_]+)"(.*?)</section>',
        html,
        re.S,
    ):
        stage, column = match.group(1), match.group(2)
        if "pipeline-card" not in column:
            continue  # empty column: nothing to carry a button
        assert ("data-log-conv" in column) is (stage in MANUAL_LOG_STAGES), stage


# ---------- the HubSpot copy ----------


def _hubspot_notes(monkeypatch, *, fails: bool = False) -> list[dict]:
    """Capture what would be written to the contact's HubSpot timeline."""
    written: list[dict] = []

    class Client:
        async def create_interaction_note(self, contact_id, body, happened_at=None, ticket_id=None):
            if fails:
                raise RuntimeError("HubSpot 500")
            written.append({"contact_id": contact_id, "body": body, "ticket_id": ticket_id})
            return "note-1"

    monkeypatch.setattr("src.integrations.hubspot.HubSpotClient", lambda: Client())
    return written


def test_a_record_reaches_the_hubspot_timeline_with_its_ticket(log_db, monkeypatch):
    """Somebody else opens this contact in HubSpot. Without the copy, a customer we have
    been talking to for weeks reads there as one nobody touched after the first reply.

    It carries the channel and who handled it, and hangs off the TICKET when the record
    was filed against one — on the contact alone, the 문의 it belongs to shows nothing.
    """
    factory, ids = log_db
    with factory() as session:
        session.get(Contact, ids["contact"]).hubspot_contact_id = "hs-42"
        session.get(Conversation, ids["negotiating"]).hubspot_ticket_id = "T-99"
        session.commit()
    written = _hubspot_notes(monkeypatch)

    with TestClient(app) as client:
        _post(
            client,
            ids["contact"],
            channel="phone",
            handler="박세일",
            summary="전화로 단가 재확인.",
            conversation_id=str(ids["negotiating"]),
        )

    assert len(written) == 1
    assert written[0]["contact_id"] == "hs-42"
    assert written[0]["ticket_id"] == "T-99"
    assert "[전화]" in written[0]["body"]
    assert "담당 박세일" in written[0]["body"]
    assert "전화로 단가 재확인." in written[0]["body"]


def test_a_hubspot_failure_never_loses_the_record(log_db, monkeypatch):
    """The copy is best effort in the only direction that matters. The record is ours;
    HubSpot gets a copy of it, and a copy must never be able to take the original."""
    factory, ids = log_db
    with factory() as session:
        session.get(Contact, ids["contact"]).hubspot_contact_id = "hs-42"
        session.commit()
    _hubspot_notes(monkeypatch, fails=True)

    with TestClient(app) as client:
        response = _post(client, ids["contact"], summary="HubSpot 이 죽어 있던 날의 기록")

    assert response.status_code == 303
    with factory() as session:
        assert session.query(CustomerInteraction).count() == 1


# ---------- the card opens its own ticket ----------


def test_a_card_opens_the_ticket_it_stands_for(log_db):
    """Not the customer page: for a repeat customer that is a different thing, and the
    card is one inquiry.

    **대화 id 로 엽니다(`/tickets/:id`).** 예전에는 그 티켓의 마지막 메일 id 를 실어
    보내고 그것이 없으면 고객 페이지로 보냈는데, 메일이 없는 티켓은 `hubspot_backfill`
    이 만든 것 — 즉 HubSpot 에서 들여온 Won·Lost 티켓 — 이었습니다. Deal Detail 도 소통
    기록도 티켓의 값이라, 하필 그 카드만 아무것도 못 고치는 자리로 갔습니다.
    """
    _factory, ids = log_db
    with TestClient(app) as client:
        board = client.get("/api/ui/dashboard").json()
    cards = [card for stage in board["stages"] for card in stage["cards"]]
    assert {card["conversation_id"] for card in cards} >= {
        ids["negotiating"], ids["won"]
    }
    # 메일 id 는 더 이상 카드에 실리지 않습니다 — 그것이 없으면 갈 곳이 없던 값입니다.
    assert all("link_message_id" not in card for card in cards)
    source = pathlib.Path("frontend/src/ui/Board.tsx").read_text(encoding="utf-8")
    assert "to={`/tickets/${card.conversation_id}`}" in source


def test_a_ticket_with_no_mail_still_opens(log_db):
    """HubSpot 에서 들여온 티켓에는 메일 행이 없습니다(`hubspot_backfill` 은 대화만 만듭니다).

    그래도 티켓 화면은 떠야 합니다 — 그 화면에만 Deal Detail 고르개와 소통 히스토리가 있고,
    Won·Lost 로 넘어온 티켓이 대부분 이 모양입니다. 초안 편집기와 발송 정보는 안 그립니다.
    """
    factory, ids = log_db
    with factory() as session:
        contact = session.query(Contact).first()
        bare = Conversation(contact_id=contact.id, stage="won", hubspot_ticket_id="4200999")
        session.add(bare)
        session.commit()
        bare_id = bare.id

    with TestClient(app) as client, patch("src.api.routes.messages.SessionLocal", factory):
        detail = client.get(f"/api/ui/tickets/{bare_id}").json()
    assert detail["msg"] is None                 # 편집기·발송 정보가 그려지지 않는 근거
    assert detail["thread"] == []
    assert detail["ticket"]["id"] == bare_id
    assert detail["ticket"]["ticket_id"] == "4200999"
    assert "won" in detail["deal_details"]       # Deal Detail 은 여기서 고칩니다
    assert ids                                    # 픽스처가 만든 것들은 그대로


def test_a_card_is_titled_with_the_ticket_name(log_db):
    """Not the company: this customer has two open inquiries, and the company name is
    the same word on both cards."""
    _factory, _ids = log_db
    with TestClient(app) as client:
        board = client.get("/api/ui/dashboard").json()
    assert {card["subject"] for stage in board["stages"] for card in stage["cards"]} == {
        "작년 계약 건",
        "더빙 단가 문의",
    }


def test_a_nameless_ticket_is_titled_from_its_own_mail(log_db):
    """Threads that predate the ticket-name column, and ones whose creating event
    carried no subject, still have the mail. Same fallback as 회신 및 검토, RE: and all —
    two screens naming one ticket differently is two tickets to read."""
    factory, ids = log_db
    with factory() as session:
        session.query(Conversation).filter_by(id=ids["negotiating"]).update(
            {"inquiry_subject": None}
        )
        session.commit()
    with TestClient(app) as client:
        board = client.get("/api/ui/dashboard").json()
    titles = {card["subject"] for stage in board["stages"] for card in stage["cards"]}
    assert titles == {"작년 계약 건", "더빙 단가 문의"}   # the RE: of our own reply comes off


def test_the_card_carries_the_workbook_client_id(log_db):
    """The number the operator matches against the Inbound DB sheet (e.g. 1330)."""
    _factory, ids = log_db
    with TestClient(app) as client:
        board = client.get("/api/ui/dashboard").json()
        ticket = client.get(f"/api/ui/messages/{ids['negotiating_message']}").json()
    assert {card["client_id"] for stage in board["stages"] for card in stage["cards"]} == {1330}
    assert ticket["ticket"]["client_id"] == 1330


# ---------- the record lands on the right ticket ----------


def test_a_record_filed_from_a_card_belongs_to_that_inquiry(log_db):
    factory, ids = log_db
    with TestClient(app) as client:
        response = _post(
            client,
            ids["contact"],
            channel="whatsapp",
            summary="왓츠앱으로 견적 재확인",
            conversation_id=str(ids["negotiating"]),
            redirect_to="/",
        )
    assert response.status_code == 303
    # Back to the board, not to the customer page the endpoint lives under.
    assert response.headers["location"] == "/"
    with factory() as session:
        record = session.query(CustomerInteraction).one()
        assert record.conversation_id == ids["negotiating"]
        assert record.channel == "whatsapp"


def test_a_contact_level_record_stays_unattached(log_db):
    """The 리드 히스토리 form sends no ticket, and that record belongs to the customer."""
    factory, ids = log_db
    with TestClient(app) as client:
        response = _post(client, ids["contact"], summary="회사 소개 자료 발송")
    assert response.status_code == 303
    assert response.headers["location"] == f"/customers/{ids['contact']}#history"
    with factory() as session:
        assert session.query(CustomerInteraction).one().conversation_id is None


def test_another_customers_ticket_cannot_be_used(log_db):
    """conversation_id is a hidden form field, so it is checked against this contact."""
    factory, ids = log_db
    with factory() as session:
        stranger = Contact(
            normalized_email="other@other.com", email="other@other.com", full_name="Other"
        )
        session.add(stranger)
        session.flush()
        stranger_id = stranger.id
        session.commit()
    with TestClient(app) as client:
        response = _post(
            client, stranger_id, summary="남의 티켓", conversation_id=str(ids["negotiating"])
        )
    assert response.status_code == 303
    with factory() as session:
        # Kept as a customer-wide note rather than filed under someone else's inquiry.
        assert session.query(CustomerInteraction).one().conversation_id is None


def test_the_return_address_can_only_be_this_site(log_db):
    _factory, ids = log_db
    with TestClient(app) as client:
        response = _post(
            client, ids["contact"], summary="기록", redirect_to="https://evil.example/steal"
        )
    assert response.headers["location"] == f"/customers/{ids['contact']}#history"


def test_a_protocol_relative_return_address_is_refused(log_db):
    """//host is a URL a browser follows off-site, and it starts with "/"."""
    _factory, ids = log_db
    with TestClient(app) as client:
        response = _post(client, ids["contact"], summary="기록", redirect_to="//evil.example")
    assert response.headers["location"] == f"/customers/{ids['contact']}#history"


# ---------- the ticket screen shows its own log ----------


def test_the_ticket_screen_separates_its_own_records_from_the_rest(log_db):
    """The ticket's 소통 히스토리 holds this inquiry's records; the sidebar keeps the rest of
    the customer. A record must appear in exactly one of the two, or every call logged
    here would render twice on one screen."""
    factory, ids = log_db
    with factory() as session:
        session.add_all(
            [
                CustomerInteraction(
                    contact_id=ids["contact"],
                    conversation_id=ids["negotiating"],
                    channel="phone",
                    direction="outgoing",
                    summary="전화로 납기 협의",
                ),
                CustomerInteraction(
                    contact_id=ids["contact"],
                    conversation_id=ids["won"],
                    channel="sms",
                    direction="incoming",
                    summary="작년 건 문자 확인",
                ),
            ]
        )
        session.commit()
    from src.api.routes.messages import _message_detail_context

    ctx = _message_detail_context(ids["negotiating_message"])
    assert [row["summary"] for row in ctx["ticket_interactions"]] == ["전화로 납기 협의"]
    sidebar = [row["summary"] for row in ctx["customer"]["interactions"]]
    assert sidebar == ["작년 건 문자 확인"]

    with TestClient(app) as client:
        payload = client.get(f"/api/ui/messages/{ids['negotiating_message']}").json()
    assert [row["summary"] for row in payload["ticket_interactions"]] == ["전화로 납기 협의"]
    assert payload["ticket"]["id"] == ids["negotiating"]


# ---------- logging a meeting must not drag a card backwards ----------


def test_a_meeting_logged_on_a_won_ticket_leaves_it_where_it_is(log_db):
    """The board is where stages move. A call note on a Won card used to reset it to
    협의 중, because the rule moved the contact's newest thread from wherever it was."""
    factory, ids = log_db
    with TestClient(app) as client:
        _post(
            client,
            ids["contact"],
            channel="meeting",
            summary="사후 미팅",
            conversation_id=str(ids["won"]),
        )
    with factory() as session:
        assert session.get(Conversation, ids["won"]).stage == "won"


def test_a_meeting_still_advances_a_thread_that_has_not_started_negotiating(log_db):
    factory, ids = log_db
    with factory() as session:
        session.get(Conversation, ids["negotiating"]).stage = "meeting_link_sent"
        session.commit()
    with TestClient(app) as client:
        _post(
            client,
            ids["contact"],
            channel="meeting",
            summary="데모 미팅",
            conversation_id=str(ids["negotiating"]),
        )
    with factory() as session:
        assert session.get(Conversation, ids["negotiating"]).stage == "negotiation"
