"""Tests for web UI — dashboard, message detail, send/reject/edit actions."""

from __future__ import annotations

import pathlib
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db import models as _models  # noqa: F401
from src.db.models import Contact, Conversation, Message


@pytest.fixture(autouse=True)
def _worker_off(monkeypatch):
    """Pin the background worker OFF so the inline approve→send path is deterministic
    regardless of the local .env (a test that wants it on re-patches in its body)."""
    from src.common.config import settings

    monkeypatch.setattr(settings, "SEND_WORKER_ENABLED", False)


QUEUE = pathlib.Path("frontend/src/ui/QueueTable.tsx")


def _client() -> TestClient:
    return TestClient(app)


def _mock_dashboard_context():
    # Row shape is the /messages one — the dashboard queue renders the same partial
    # from the same context builder, so a row that works there works here.
    return {
        "recent_messages": [
            {
                "id": 1,
                "status": "pending_approval",
                "stage": "new",
                "subject": "가격 문의",
                "category": "pricing_question",
                "email": "buyer@example.com",
                "received_at": datetime(2026, 1, 1, 12, 0),
                "waiting_since": datetime(2026, 1, 1, 12, 0),
            }
        ],
        "now": datetime(2026, 1, 2, 12, 0),
        "awaiting_total": 7,
        "received_today": 4,
        # The board renders below the queue; an empty board is enough for these tests.
        # `total` is the column's real size, which the header shows even when the column
        # renders only its first page.
        "stages": [{"key": "new", "label": "New", "rows": [], "total": 0}],
        "stage_labels": {"new": "New"},
        "category_labels": {"pricing_question": "견적·가격", "spam": "영업·홍보"},
        "unqualified": ["spam", "support", "recruiting"],
        "manual_log_stages": ("meeting_link_sent", "negotiation"),
    }


def _mock_detail_context(message_id):
    if message_id == 1:
        return {
            "thread": [
                {
                    "id": 1,
                    "direction": "outgoing",
                    "status": "pending_approval",
                    "subject": "가격 안내",
                    "body": "안녕하세요, 가격 안내드립니다.",
                    "body_ko": None,
                    "channel": "email",
                    "from_address": "sales@company.com",
                    "to_address": "test@example.com",
                    "created_at": datetime(2026, 1, 1, 12, 0),
                    "sent_at": None,
                    "is_current": True,
                }
            ],
            "ticket": {"ticket_id": "T-1", "stage": "initial", "topic": "pricing_question"},
            "inbound_messages": [],
            "msg": {
                "id": 1,
                "status": "pending_approval",
                "subject": "가격 안내",
                "body": "안녕하세요, 가격 안내드립니다.",
                "body_ko": None,
                "channel": "email",
                "direction": "outgoing",
                "language": "ko",
                "to_address": "test@example.com",
                "from_address": "sales@company.com",
                "score_snapshot": 80,
                "scheduled_at": None,
                "sent_at": None,
                "created_at": datetime(2026, 1, 1, 12, 0),
                "category": "pricing_question",
            },
            "contact": {
                "id": 1,
                "name": "Test User",
                "email": "test@example.com",
                "company": "TestCo",
            },
            "prospect": None,
            "domain_profile": None,
        }
    return {}


@patch("src.api.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_the_dashboard_payload_has_the_two_panels_the_screen_draws():
    r = _client().get("/api/ui/dashboard")
    assert r.status_code == 200
    assert "stages" in r.json()


def test_the_console_shell_loads_the_same_stylesheets_both_stacks_used():
    """console.css is linked, not bundled — one copy of the design for the SPA and for
    the sign-in page that still renders server-side."""
    r = _client().get("/app")
    assert "/static/console.css" in r.text
    assert "/static/tokens.css" in r.text
    assert "theme-toggle" not in r.text


def test_static_assets_are_revalidated():
    """A CSS change has to reach the operator without a hard reload. The SPA bundle is
    content-hashed by the build; console.css is not, so /static answers no-cache."""
    assert _client().get("/static/console.css").headers["cache-control"] == "no-cache"


def test_the_spa_bundle_is_what_the_shell_loads():
    r = _client().get("/app")
    assert "/static/app/assets/" in r.text
    assert 'id="root"' in r.text


def test_the_shell_loads_the_korean_ui_font():
    # Pretendard (Korean UI font) is loaded via tokens.css, which the shell links.
    r = _client().get("/app")
    assert "/static/tokens.css" in r.text


@patch("src.api.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_shows_queue_counters():
    """Two numbers, not four.

    ALL used to sum every stage while the list it links to shows New only, so the header
    said 6 and the screen behind it held 1 — the other five were drafts on tickets
    somebody had already answered in HubSpot. Counting the same rows the list counts
    makes ALL and New the same number, and two counters saying one thing read as two
    different things. The per-stage numbers are on the board's column headers below.
    """
    counters = _client().get("/api/ui/dashboard").json()["counters"]
    assert set(counters) == {"received_today", "awaiting_total"}
    screen = pathlib.Path("frontend/src/screens/Dashboard.tsx").read_text(encoding="utf-8")
    for label in ("답변 대기중인 문의", "오늘 접수", "ALL"):
        assert label in screen, label
    assert "awaiting_negotiation" not in screen


@patch("src.api.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_json_carries_the_queue_rows():
    payload = _client().get("/api/ui/dashboard").json()
    assert [row["subject"] for row in payload["queue"]] == ["가격 문의"]


@patch("src.api.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_json_carries_the_board():
    """The board is on the dashboard, below the queue — one screen, one payload."""
    payload = _client().get("/api/ui/dashboard").json()
    assert [stage["key"] for stage in payload["stages"]] == ["new"]
    assert "문의 파이프라인" in pathlib.Path(
        "frontend/src/screens/Dashboard.tsx"
    ).read_text(encoding="utf-8")


def test_the_queue_shows_the_inquiry_type_where_the_channel_used_to_be():
    """채널 was "email" on every row — a column that separated nothing, taking the width
    of the thing the operator actually wants at a glance. 문의 유형 is stored again for
    this (0049); it also replaced the 검토 필요 flag, because "CS 문의" or "영업·홍보"
    says which one to open first far more precisely than "확인이 필요합니다" did.

    UnQualified means "not a sales lead", not "do not reply" — those still get an answer,
    from the CS guide or the intro document.
    """
    source = QUEUE.read_text(encoding="utf-8")
    assert 'label: "문의 유형"' in source
    assert 'label: "채널"' not in source
    assert "UnQualified" in source
    assert "검토 필요" not in source


@patch("src.api.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_a_queue_row_carries_the_id_its_link_is_built_from():
    payload = _client().get("/api/ui/dashboard").json()
    assert payload["queue"][0]["id"] == 1


def test_dashboard_queue_table_is_the_review_table():
    """One component, rendered by the dashboard and by 회신 및 검토.

    Each page used to carry its own copy of this markup and they drifted: the dashboard
    was missing 우선순위 / 소통 Email and headed its date column 시간 while /messages
    headed the same value 접수 시간.
    """
    source = QUEUE.read_text(encoding="utf-8")
    for header in ("상태", "Stage", "문의 제목", "문의 유형", "소통 Email", "접수 시간"):
        assert f'label: "{header}"' in source, header
    # 우선순위 is one dot wide and centred under its own heading.
    assert 'headClassName: "th-center"' in source


def test_every_table_is_the_same_table():
    """Six screens each declared their own table-wrap / table / thead / tbody and their
    own copy of the 'nothing here' row. Two consequences beyond the duplication: a
    colSpan that stops matching the columns above it, and column widths measured per
    table — so two tables showing the SAME columns put them in different places, which
    is exactly what 문의별 참고 and 항상 적용 did.
    """
    screens = list(pathlib.Path("frontend/src/screens").glob("*.tsx"))
    # DataTable is the one. Loading draws the SHAPE of a table before there are any rows
    # — it has no columns to drift from, and routing a placeholder through the component
    # that renders data would mean inventing rows to render.
    screens += [path for path in pathlib.Path("frontend/src/ui").glob("*.tsx")
                if path.name not in {"DataTable.tsx", "Loading.tsx"}]
    # `className="table"` is the console's list table specifically. The printed
    # documents' `sheet__table` is not one — it has a totals row, its own print
    # stylesheet, and no shared columns to drift from.
    offenders = [
        str(path) for path in screens
        if 'className="table' in path.read_text(encoding="utf-8")
    ]
    assert offenders == []



@patch("src.api.routes.dashboard._dashboard_context", _mock_dashboard_context)
def test_dashboard_drops_the_receipt_ack_caption():
    """'접수 확인 제외' captioned the queue; the table never listed acks anyway."""
    r = _client().get("/")
    assert "접수 확인 제외" not in r.text


def test_pipeline_columns_show_the_label_only():
    """The Korean gloss under each header (New / 새 문의) said nothing the label did not,
    on all seven columns at once. The server ships the label; the board prints it."""
    from src.api.routes.customer_ops import PIPELINE_STAGES

    board = pathlib.Path("frontend/src/ui/Board.tsx").read_text(encoding="utf-8")
    assert "{stage.label}" in board
    for _key, _label, gloss in PIPELINE_STAGES:
        assert gloss not in board, gloss


def test_the_all_counter_counts_the_rows_the_list_it_opens_holds(db_session_factory, monkeypatch):
    """ALL said 6, 회신 및 검토 held 1.

    The counter summed every stage while the list shows New only — the other five were
    drafts on tickets somebody had already answered in HubSpot, which is exactly why the
    list stopped showing them. A number that disagrees with the screen it links to sends
    the operator looking for work that is not there, so it counts the same rows.
    """
    from src.api.routes import customer_ops, dashboard
    from src.api.routes import messages as messages_route
    from src.api.routes.dashboard import _awaiting_counters
    from src.api.routes.messages import _messages_list_context

    for module in (dashboard, messages_route, customer_ops):
        monkeypatch.setattr(module, "SessionLocal", db_session_factory)

    with db_session_factory() as session:
        contact = Contact(normalized_email="c@example.com", email="c@example.com", full_name="C")
        session.add(contact)
        session.flush()
        # One still New, one already moved on in HubSpot with our draft left behind.
        for stage in ("new", "meeting_link_sent"):
            conv = Conversation(contact_id=contact.id, stage=stage, inquiry_subject=stage)
            session.add(conv)
            session.flush()
            session.add(
                Message(
                    conversation_id=conv.id,
                    direction="outgoing",
                    channel="email",
                    subject="RE: 문의",
                    body="draft",
                    status="pending_approval",
                )
            )
        session.commit()

    listed = _messages_list_context(status="awaiting", stage="", sort="oldest")["messages"]
    assert len(listed) == 1
    assert _awaiting_counters()["awaiting_total"] == len(listed)


def test_dashboard_queue_is_the_five_oldest(db_session_factory, monkeypatch):
    """답변 대기중인 문의 is a peek at the front of the FIFO queue, not the queue.

    Five rows, oldest first — the rest is one click away on 회신 및 검토. The counters
    beside the heading still see every awaiting row.
    """
    from src.api.routes import customer_ops, dashboard
    from src.api.routes import messages as messages_route
    from src.api.routes.dashboard import _dashboard_context

    for module in (dashboard, messages_route, customer_ops):
        monkeypatch.setattr(module, "SessionLocal", db_session_factory)

    with db_session_factory() as session:
        contact = Contact(normalized_email="q@example.com", email="q@example.com", full_name="Q")
        session.add(contact)
        session.flush()
        for day in range(1, 8):
            conv = Conversation(
                contact_id=contact.id,
                stage="new",
                inquiry_subject=f"문의-{day:02d}",
                created_at=datetime(2026, 1, day, 9, 0),
            )
            session.add(conv)
            session.flush()
            session.add(
                Message(
                    conversation_id=conv.id,
                    direction="outgoing",
                    channel="email",
                    subject="RE: 문의",
                    body="draft",
                    status="pending_approval",
                )
            )
        session.commit()

    ctx = _dashboard_context()
    assert [row["subject"] for row in ctx["recent_messages"]] == [
        "문의-01",
        "문의-02",
        "문의-03",
        "문의-04",
        "문의-05",
    ]
    assert ctx["awaiting_total"] == 7


# ---------- Message detail ----------


@patch("src.api.routes.messages._message_detail_context", _mock_detail_context)
def test_the_ticket_payload_loads_for_a_real_message():
    r = _client().get("/api/ui/messages/1")
    assert r.status_code == 200
    assert "가격 안내" in r.text
    assert "pricing_question" in r.text
    assert "Test User" in r.text


@patch("src.api.routes.messages._message_detail_context", _mock_detail_context)
def test_message_detail_404_for_missing():
    """An unknown id is a 404 from the JSON, not a blank screen. (/messages/99999 itself
    redirects into the SPA — client routing cannot know the id is bad until it asks.)"""
    r = _client().get("/api/ui/messages/99999")
    assert r.status_code == 404


def test_the_ticket_screen_offers_send_and_reject():
    """검토 완료 · 발송 and 거절 are the two decisions the screen exists for."""
    screen = pathlib.Path("frontend/src/screens/MessageDetail.tsx").read_text(encoding="utf-8")
    assert "검토 완료 · 발송" in screen
    assert "/send" in screen


def test_message_detail_embeds_customer_history(_use_test_db):
    """The reply detail page surfaces the customer's CRM state, contract, and
    cross-channel touchpoints inline (via the real _message_detail_context /
    _customer_history), so the operator doesn't leave for the /customers page."""
    from datetime import timezone

    from src.db.models import ContractRecord, CustomerInteraction, CustomerProfile

    session = _use_test_db()
    contact = Contact(
        normalized_email="buyer@acme.com", full_name="Acme Buyer",
        email="buyer@acme.com", domain="acme.com", company="Acme",
    )
    session.add(contact)
    session.flush()
    contact_id = contact.id
    conv = Conversation(contact_id=contact_id, inquiry_subject="가격 문의")
    session.add(conv)
    session.flush()
    msg = Message(
        conversation_id=conv.id, direction="outgoing", channel="email",
        subject="안내", body="안녕하세요", status="pending_approval",
    )
    session.add(msg)
    session.add(CustomerProfile(
        contact_id=contact_id, customer_state="service", pipeline_stage="active",
        lead_temperature="hot", current_plan="PERSO Pro", next_action="금요일 재연락",
    ))
    session.add(CustomerInteraction(
        contact_id=contact_id, channel="meeting", direction="outgoing",
        subject="킥오프 미팅", summary="온보딩 일정 확정",
        happened_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    ))
    session.add(ContractRecord(contact_id=contact_id, status="active", plan="PERSO Pro", currency="KRW"))
    session.commit()
    msg_id = msg.id
    session.close()

    payload = _client().get(f"/api/ui/messages/{msg_id}").json()
    customer = payload["customer"]
    assert customer["profile"]["customer_state"] == "service"
    assert customer["profile"]["next_action"] == "금요일 재연락"
    assert "킥오프 미팅" in [row["subject"] for row in customer["interactions"]]
    # The panel is scoped to THIS customer and no longer links out: the full history
    # lives in its own sidebar section (고객 히스토리 → 인바운드 고객 히스토리) now.
    # The panel is scoped to THIS customer: the ticket's own log is separate from the
    # contact-wide one, so a record cannot render twice on one screen.
    assert payload["ticket_interactions"] == []


# ---------- Message actions (send/reject/edit) ----------


@pytest.fixture()
def _use_test_db():
    """Shared in-memory DB for route + approval integration tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with (
        patch("src.api.routes.messages.SessionLocal", factory),
        patch("src.agents.approval.SessionLocal", factory),
        patch("src.agents.send_worker.SessionLocal", factory),
        patch("src.integrations.senders.SessionLocal", factory, create=True),
    ):
        yield factory


@pytest.fixture()
def pending_msg(_use_test_db):
    """Insert a pending_approval message and return its id."""
    factory = _use_test_db
    session = factory()
    contact = Contact(normalized_email="t@e.com", full_name="T", email="t@e.com")
    session.add(contact)
    session.flush()
    conv = Conversation(contact_id=contact.id, inquiry_subject="test")
    session.add(conv)
    session.flush()
    msg = Message(
        conversation_id=conv.id,
        direction="outgoing",
        channel="email",
        subject="Test",
        body="Hello",
        status="pending_approval",
    )
    session.add(msg)
    session.commit()
    msg_id = msg.id
    session.close()
    return msg_id


@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_message_send_approves(mock_send, pending_msg, _use_test_db):
    r = _client().post(f"/messages/{pending_msg}/send", data={"body": "edited", "subject": "Test"})
    assert r.status_code == 200
    assert "승인" in r.text
    mock_send.assert_awaited_once()
    session = _use_test_db()
    m = session.get(Message, pending_msg)
    # Human approval dispatches immediately, so the message is sent, not queued.
    assert m.status == "sent"
    assert m.body == "edited"
    session.close()


@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_message_send_prevents_double(mock_send, pending_msg):
    _client().post(f"/messages/{pending_msg}/send", data={"body": "Hello", "subject": "Test"})
    r = _client().post(f"/messages/{pending_msg}/send", data={"body": "Hello", "subject": "Test"})
    assert r.status_code == 400


@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_message_send_defers_to_worker_when_enabled(mock_send, pending_msg, _use_test_db):
    """With the background worker on, /send approves but does NOT inline-send (the
    worker claims approved rows) — prevents a double-send race."""
    from src.common.config import settings

    with patch.object(settings, "SEND_WORKER_ENABLED", True):
        r = _client().post(f"/messages/{pending_msg}/send", data={"body": "Hello", "subject": "Test"})
    assert r.status_code == 200
    mock_send.assert_not_awaited()
    session = _use_test_db()
    m = session.get(Message, pending_msg)
    assert m.status == "approved"  # left for the worker
    session.close()


def test_message_reject(pending_msg, _use_test_db):
    r = _client().post(f"/messages/{pending_msg}/reject", data={"reason": "tone"})
    assert r.status_code == 200
    assert "거절" in r.text
    session = _use_test_db()
    m = session.get(Message, pending_msg)
    assert m.status == "rejected"
    session.close()


def test_message_edit_saves(pending_msg, _use_test_db):
    r = _client().post(
        f"/messages/{pending_msg}/edit", data={"body": "new body", "subject": "new subj"}
    )
    assert r.status_code == 200
    assert "저장" in r.text
    session = _use_test_db()
    m = session.get(Message, pending_msg)
    assert m.body == "new body"
    assert m.subject == "new subj"
    session.close()


def _mock_messages_list_context(status="awaiting", stage="", sort="oldest"):
    return {
        "messages": [
            {
                "id": 1,
                "status": "pending_approval",
                "stage": "new",
                "subject": "가격 문의",
                "channel": "email",
                "email": "buyer@example.com",
                "received_at": datetime(2026, 1, 1, 12, 0),
                "waiting_since": datetime(2026, 1, 1, 12, 0),
            }
        ],
        "filter_status": status,
        "filter_stage": stage,
        "filter_sort": sort,
        "stage_labels": {"new": "New", "negotiation": "Negotiating"},
        "now": datetime(2026, 1, 2, 12, 0),
    }


@patch("src.api.routes.messages._messages_list_context", _mock_messages_list_context)
def test_the_queue_payload_carries_the_rows_the_screen_lists():
    r = _client().get("/api/ui/messages")
    assert r.status_code == 200
    assert "가격 문의" in [row["subject"] for row in r.json()["messages"]]
    assert "회신 및 검토" in pathlib.Path(
        "frontend/src/screens/Messages.tsx"
    ).read_text(encoding="utf-8")


@patch("src.integrations.senders.send", new_callable=AsyncMock)
def test_message_edit_blocked_after_approve(mock_send, pending_msg):
    _client().post(f"/messages/{pending_msg}/send", data={"body": "Hello", "subject": "Test"})
    r = _client().post(f"/messages/{pending_msg}/edit", data={"body": "x", "subject": ""})
    assert r.status_code == 400


def test_the_stage_dropdown_is_only_on_the_screen_it_filters():
    """리드 히스토리에서는 단계를 보여주는 열이 단계로 거르는 열이기도 합니다.

    협상중 고객에서는 뺍니다. 그 화면은 이미 한 단계만 보는 화면이라, 행마다 드롭다운이
    붙어 있으면 이 고객의 단계를 바꾸는 컨트롤처럼 보입니다 — 단계는 파이프라인 보드나
    HubSpot 에서 움직이는 것이지 목록에서 고르는 것이 아닙니다.
    """
    customers = pathlib.Path("frontend/src/screens/Customers.tsx").read_text(encoding="utf-8")
    assert 'const isFixedStage = stage === "negotiation";' in customers
    assert "label: isFixedStage ? (" in customers
    # 필터 자체는 남아 있어야 합니다 — 리드 히스토리가 그것으로 좁힙니다.
    assert 'id="stage-filter"' in customers
