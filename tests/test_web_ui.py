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
    """세 숫자가 화면 맨 위, 제목 옆에 섭니다 — 오늘 접수 · 답변 대기 · 협상중.

    예전에는 「답변 대기중인 문의」 카드 머리에 두 개(오늘 접수 · ALL)가 붙어 있었습니다.
    거기 있으면 그 표에 대한 숫자로 읽히는데, 협상중은 그 표에 아예 안 나오는 단계라 넣을
    자리가 없었습니다. 2026-08-18 에 운영자 지시로 위로 올리고 협상중을 더했습니다.

    **협상중을 세는 곳을 서버에 만들지 않았습니다.** 아래 보드의 Negotiating 열 총계를 그대로
    씁니다 — 같은 화면의 두 숫자가 서로 다른 질의에서 나오면 언젠가 어긋나고, 그때 어느 쪽이
    맞는지 화면만 봐서는 알 수 없습니다. 그래서 payload 의 counters 는 그대로 둘입니다.
    """
    counters = _client().get("/api/ui/dashboard").json()["counters"]
    assert set(counters) == {"received_today", "awaiting_total"}
    screen = pathlib.Path("frontend/src/screens/Dashboard.tsx").read_text(encoding="utf-8")
    for label in ("답변 대기중인 문의", "오늘 접수", "협상중"):
        assert label in screen, label
    # 「답변 대기」만 찾으면 카드 제목 「답변 대기중인 문의」에 걸려 칩을 지워도 통과합니다.
    assert "답변 대기 <b" in screen
    # 협상중은 보드의 열 총계에서 옵니다. 서버에 세 번째 카운터를 만들면 안 됩니다.
    assert 'stage.key === "negotiation"' in screen
    assert "awaiting_negotiation" not in screen
    # 카드 머리의 옛 카운터는 사라졌습니다 — 표에 대한 숫자로 읽히던 자리입니다.
    assert "queue-counters" not in screen


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
    """검토 완료 · 발송 and 거절 are the two decisions the screen exists for.

    주소는 `act()` 한 곳에서 만듭니다 — 이 화면은 메일이 없는 티켓(HubSpot 에서 들여온 것)
    으로도 열리므로, 그 주소를 쓰는 곳마다 `msg` 가 있는지 확인하는 대신 한 곳에서 걸러냅니다.
    """
    screen = pathlib.Path("frontend/src/screens/MessageDetail.tsx").read_text(encoding="utf-8")
    assert "검토 완료 · 발송" in screen
    assert 'act("send")' in screen
    assert 'act("reject"' in screen
    assert "`/messages/${msg.id}/${action}`" in screen


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
        conversation_id=conv.id, direction="outgoing", subject="안내", body="안녕하세요", status="pending_approval",
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


def test_the_stage_dropdown_is_always_there():
    """리드 히스토리에서는 단계를 보여주는 열이 단계로 거르는 열이기도 합니다.

    한동안 이 열에 분기가 있었습니다 — 사이드바의 「협상중 고객」이 같은 화면을
    `?stage=negotiation` 으로 열었고, 그때는 드롭다운 대신 `Stage(Negotiating)` 글자만
    보여 줬습니다. 그 항목이 지워진(2026-08-18, 운영자 지시) 지금 분기가 남아 있으면
    거꾸로 덫입니다: 드롭다운에서 Negotiating 을 고르는 순간 드롭다운 자신이 사라져
    「전체」로 돌아갈 길이 없어집니다.
    """
    customers = pathlib.Path("frontend/src/screens/Customers.tsx").read_text(encoding="utf-8")
    assert "isFixedStage" not in customers
    assert 'id="stage-filter"' in customers


def test_the_console_does_not_ask_more_often_than_anything_can_change():
    """왕복 하나가 200ms 인 배포(앱 Oregon · DB 도쿄)에서 재요청은 공짜가 아닙니다.

    세 가지가 겹쳐 있었습니다:

    - 포커스 재요청: 알트탭 한 번마다 화면에 뜬 질의를 전부 다시 받았습니다. SSE 가 이미
      "서버에서 뭔가 바뀌었다" 를 알려 주므로 하는 일이 같습니다.
    - 저장 한 번에 재요청 두 번: 쓴 탭이 스스로 invalidate 하고, 곧이어 자기가 일으킨 SSE
      이벤트를 받아 또 했습니다.
    - 15초 폴링: 사람 손 없이 바뀌는 것 중 가장 빠른 것이 HubSpot 폴러이고 주기가 600초
      입니다. 15초마다 물어도 10분에 한 번 오는 것이 더 빨리 오지 않습니다.

    폴링을 아예 없애지 않은 이유는 SSE 가 **HTTP 쓰기에만** 붙어 있기 때문입니다 — 백그라운드
    작업은 요청이 아니라 알려 오지 않습니다. 그래서 느린 그물로 남깁니다.
    """
    import pathlib

    api = pathlib.Path("frontend/src/lib/api.ts").read_text(encoding="utf-8")
    assert "refetchOnWindowFocus: false" in api
    assert "setTimeout(" in api and "clearTimeout(" in api, "SSE 이벤트를 묶지 않고 있습니다"

    for screen in ("Messages", "Dashboard", "Recovery", "Simple"):
        source = pathlib.Path(f"frontend/src/screens/{screen}.tsx").read_text(encoding="utf-8")
        for faster in ("refetchInterval: 15_000", "refetchInterval: 30_000"):
            assert faster not in source, f"{screen}: {faster}"


def test_the_signature_preview_is_a_snapshot_not_a_live_mirror():
    """미리보기는 늘 켜져 있지만 srcDoc 은 여전히 스냅샷입니다.

    본문 상태에 직접 묶으면 글자를 칠 때마다 iframe 이 문서를 통째로 다시 싣습니다 —
    타자 한 번에 리로드 한 번. 타자가 멎으면 따라오게 하되(디바운스), 묶지는 않습니다.
    타이머는 반드시 정리해야 합니다: 안 그러면 글자 수만큼 타이머가 쌓입니다.
    """
    import pathlib

    source = pathlib.Path("frontend/src/screens/EmailTemplates.tsx").read_text(encoding="utf-8")
    assert "${preview}</body>" in source
    assert "${body}</body>" not in source
    assert "setTimeout(() => setPreview(body)" in source
    assert "clearTimeout(timer)" in source


def test_a_button_that_waits_says_so_on_itself():
    """누른 버튼이 말합니다 — 카드 아래 "저장 중…" 이 아니라.

    반응이 누른 자리와 다른 곳에 있으면 눌린 건지 알 수 없어서 한 번 더 누르게 되고, 두 번째
    클릭은 같은 요청을 하나 더 보냅니다. 승인·발송에서는 그게 두 번 나가는 것과 같습니다.

    그래서 **쓰기를 하는 화면**은 진행 표시를 그 자리에 들고 있어야 합니다: 공용 헬퍼
    (ui/ActionButton) 를 쓰거나, 자기 버튼에 spinner 를 직접 그리거나.
    """
    import pathlib

    silent: list[str] = []
    for path in sorted(pathlib.Path("frontend/src").rglob("*.tsx")):
        source = path.read_text(encoding="utf-8")
        writes = "postForm(" in source or any(
            f'method: "{verb}"' in source for verb in ("POST", "PUT", "DELETE")
        )
        if not writes:
            continue
        if "ActionButton" in source or 'className="spinner"' in source:
            continue
        silent.append(path.as_posix())
    assert silent == [], f"진행 표시가 없는 쓰기 화면: {silent}"


def test_no_hook_is_called_after_an_early_return():
    """React 훅은 렌더마다 **같은 순서로 같은 수**만큼 불려야 합니다.

    early return 아래에 훅을 두면 로딩 렌더에서는 건너뛰고 데이터가 온 렌더에서는 부르게
    되어, React 가 "훅 수가 달라졌다"(#310) 로 터집니다 — 그 화면이 통째로 안 뜹니다.
    빌드는 이걸 못 잡고, 실제로 접근 승인 화면이 그렇게 죽은 채 배포됐습니다.

    "컴포넌트 본문의 return" 만 셉니다: 들여쓰기 2칸짜리 return, 그리고 2칸짜리 ``if (`` 블록
    안의 4칸짜리 return. 콜백 안의 ``if (!tier) return;`` 은 컴포넌트를 끝내지 않으므로
    세지 않습니다 — 그것까지 세면 멀쩡한 화면이 걸립니다.
    """
    import pathlib
    import re

    component = re.compile(r"^(?:export )?function ([A-Z]\w*)\(")
    hook = re.compile(r"^ {2}(?:const .*= )?use[A-Z]\w*\(")
    guard_open = re.compile(r"^ {2}(?:\} else )?if \(.*\{\s*$")
    return_2 = re.compile(r"^ {2}(?:if \(.*\) )?return")
    return_4 = re.compile(r"^ {4}return")

    offenders: list[str] = []
    for path in sorted(pathlib.Path("frontend/src").rglob("*.tsx")):
        name, returned, in_guard = None, False, False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = component.match(line)
            if match:
                name, returned, in_guard = match.group(1), False, False
                continue
            if name is None:
                continue
            if line.startswith("}"):  # 함수가 끝났습니다
                name, returned, in_guard = None, False, False
                continue
            if guard_open.match(line):
                in_guard = True
            elif line.startswith("  }"):
                in_guard = False
            if return_2.match(line) or (in_guard and return_4.match(line)):
                returned = True
            elif returned and hook.match(line):
                offenders.append(f"{path.as_posix()}:{number} ({name}) {line.strip()[:60]}")
    assert not offenders, "early return 뒤의 훅: " + " / ".join(offenders)


def test_a_modal_closes_only_when_the_scrim_is_pressed_and_released():
    """열자마자 닫히는 것을 막습니다.

    click 하나로 판단하면, 누른 곳과 뗀 곳이 다를 때 브라우저가 **그 둘의 공통 조상**에
    click 을 보내는 성질 때문에 엉뚱하게 닫힙니다. 여는 버튼을 누른 손이 모달이 뜬 자리에서
    떼지면 열리자마자 닫혔습니다 — "번쩍 했다가 사라져". 본문에서 글자를 드래그하다 배경에서
    떼는 경우도 같습니다.

    누르고 뗀 것이 **둘 다** 배경일 때만 닫습니다.
    """
    import pathlib

    modal = pathlib.Path("frontend/src/ui/Modal.tsx").read_text(encoding="utf-8")
    assert "onMouseDown" in modal
    assert "downOnScrim" in modal
    assert "downOnScrim.current" in modal
    # click 만 보고 닫던 옛 모양이 돌아오면 안 됩니다.
    assert "onClick={(e) => e.target === e.currentTarget && onClose()}" not in modal


def test_typing_in_a_modal_blocks_the_accidental_close():
    """스치듯 누른 Escape 하나에 다 쓴 폼이 사라지면 안 됩니다.

    계약 폼은 칸이 서른 개가 넘고, 닫기가 곧 라우트 이동이라 되돌릴 방법이 없습니다. 소통
    기록 폼은 값을 DOM 에만 들고 있어 더합니다. `취소` 버튼은 언제나 닫습니다 — 그건 실수로
    누르는 자리가 아닙니다.

    기준이 **입력 이벤트**인 이유: 값 비교(`value !== defaultValue`)를 먼저 썼는데, React 가
    제어 입력의 value 속성까지 갱신해서 늘 "안 고쳐졌다" 가 되고, 반대로 `select` 는
    `selected` 속성을 안 달아 갓 연 폼의 드롭다운 일곱 개가 전부 "고쳐졌다" 가 됩니다.
    """
    import pathlib

    modal = pathlib.Path("frontend/src/ui/Modal.tsx").read_text(encoding="utf-8")
    assert "onInput" in modal
    assert "typed.current" in modal
    # 확인 창(입력칸 없음)은 여전히 Escape 로 닫혀야 하므로, 기본값은 닫는 쪽입니다.
    assert "const typed = useRef(false);" in modal


def test_the_board_move_answers_where_the_banner_can_read_it():
    """보드는 응답의 **최종 주소**에서 ?sync 를 읽습니다(SyncBanner.syncStateFrom).

    `/?sync=ok` 로 보내면 legacy_redirects 가 `/app` 으로 한 번 더 넘기면서 쿼리를
    떨어뜨려, 성공했을 때 배너가 한 번도 뜨지 않았습니다 — 운영자가 본 것은 요청이
    실패했을 때의 "partial" 뿐이었습니다.
    """
    from src.api.routes import customer_ops

    assert customer_ops._BOARD_REDIRECT.startswith("/app?sync=")
    # 그 주소가 정말 최종인지: 다시 넘기는 표에 /app 이 없어야 합니다.
    from src.api.routes.legacy_redirects import _MOVED

    assert not any(src == "/app" for src, _dst in _MOVED)


def test_the_event_stream_never_drops_someone_elses_change():
    """SSE 이벤트에는 누가 썼는지가 없습니다(토픽은 경로뿐). 시간으로 자기 메아리를
    걸러 내려 하면 남의 저장까지 같이 버려지고, 포커스 재요청이 꺼져 있어 되받을 길이
    없는 화면이 있습니다."""
    import pathlib

    api = pathlib.Path("frontend/src/lib/api.ts").read_text(encoding="utf-8")
    source = api[api.index("source.onmessage") :]
    assert "lastLocalWrite" not in source
