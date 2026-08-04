"""Customer history, pipeline, interaction, and contract UI tests."""

from __future__ import annotations

import pathlib

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Numeric, create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db.models import (
    Contact,
    ContractRecord,
    Conversation,
    CustomerInteraction,
    CustomerProfile,
    Message,
)


@pytest.fixture()
def customer_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with patch("src.api.web.routes.customer_ops.SessionLocal", factory):
        yield factory


@pytest.fixture()
def customer_id(customer_db) -> int:
    with customer_db() as session:
        contact = Contact(
            normalized_email="buyer@example.com",
            email="buyer@example.com",
            full_name="Buyer Kim",
            company="Example Co",
            domain="example.com",
        )
        session.add(contact)
        session.flush()
        session.add(
            Conversation(
                contact_id=contact.id,
                stage="initial",
                last_incoming_at=datetime.now() - timedelta(days=20),
                # Backdated too: 최근 활동 is the latest of the three timestamps, so a row
                # stamped "now" is never stale however old its last inbound is.
                created_at=datetime.now() - timedelta(days=21),
            )
        )
        session.commit()
        return contact.id


def test_customer_list_and_detail(customer_db, customer_id) -> None:
    with TestClient(app) as client:
        listing = client.get("/api/ui/customers").json()
        detail = client.get(f"/api/ui/customers/{customer_id}").json()
    assert "Example Co" in [row["company"] for row in listing["rows"]]
    assert detail["contact"]["company"] == "Example Co"
    # The screen's two sections need these: the timeline, and the inquiry a contract
    # gets attached to.
    assert "timeline" in detail
    assert "conversations" in detail


def test_recent_activity_is_the_latest_thing_that_happened(customer_db) -> None:
    """The column is headed 최근 활동 and the list sorts on it, so it has to mean the
    latest of the three timestamps. `incoming or outgoing` returned the customer's own
    message even when our reply came after it: a thread answered today reported the
    inquiry's date and sank below threads nobody had touched in a week."""
    from src.api.web.routes.customer_ops import _customer_rows

    with customer_db() as session:
        for email, incoming, outgoing in (
            # Answered today, but the customer wrote a week ago.
            ("answered@example.com", datetime(2026, 7, 28), datetime(2026, 8, 4)),
            # Nobody has touched this one since the customer wrote.
            ("stale@example.com", datetime(2026, 8, 1), None),
        ):
            contact = Contact(normalized_email=email, email=email, full_name=email)
            session.add(contact)
            session.flush()
            session.add(
                Conversation(
                    contact_id=contact.id,
                    stage="negotiation",
                    last_incoming_at=incoming,
                    last_outgoing_at=outgoing,
                    created_at=datetime(2026, 7, 20),
                )
            )
        session.commit()

    rows = _customer_rows()
    assert [row["contact"].email for row in rows] == [
        "answered@example.com",
        "stale@example.com",
    ]
    assert rows[0]["last_activity"] == datetime(2026, 8, 4)


def test_profile_and_meeting_move_pipeline(customer_db, customer_id) -> None:
    with TestClient(app) as client:
        response = client.post(
            f"/customers/{customer_id}/profile",
            data={"customer_state": "negotiation", "pipeline_stage": "new"},
            follow_redirects=False,
        )
        meeting = client.post(
            f"/customers/{customer_id}/interactions",
            data={"channel": "meeting", "direction": "note", "summary": "Demo booked"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert meeting.status_code == 303
    with customer_db() as session:
        profile = session.get(CustomerProfile, customer_id)
        assert profile.pipeline_stage == "negotiation"
        assert session.query(CustomerInteraction).count() == 1


@pytest.fixture()
def customer_db_prod_session():
    """Session factory with expire_on_commit=True, matching the production
    sessionmaker. Regression guard for the DetachedInstanceError that only
    surfaces when ORM attributes are read after the `with SessionLocal()` block —
    the default customer_db fixture uses expire_on_commit=False and hides it."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=True)
    with patch("src.api.web.routes.customer_ops.SessionLocal", factory):
        yield factory


def test_profile_and_stage_survive_expired_session(customer_db_prod_session) -> None:
    factory = customer_db_prod_session
    with factory() as session:
        contact = Contact(
            normalized_email="prod@example.com",
            email="prod@example.com",
            full_name="Prod Buyer",
            company="Prod Co",
            domain="example.com",
            sheet_client_id=555,
        )
        session.add(contact)
        session.flush()
        session.add(
            Conversation(
                contact_id=contact.id,
                stage="initial",
                hubspot_ticket_id="T-1",
                sheet_client_id=555,
            )
        )
        session.commit()
        cid = contact.id
    # Both handlers read latest_ticket/contact attributes after committing; with
    # expire_on_commit=True that raised DetachedInstanceError (→ 500) before the
    # primitives were captured inside the session block.
    with TestClient(app) as client:
        profile = client.post(
            f"/customers/{cid}/profile",
            data={"customer_state": "negotiation", "pipeline_stage": "new"},
            follow_redirects=False,
        )
        meeting = client.post(
            f"/customers/{cid}/interactions",
            data={"channel": "meeting", "direction": "note", "summary": "Demo booked"},
            follow_redirects=False,
        )
    assert profile.status_code == 303
    assert meeting.status_code == 303


def test_active_contract_marks_service_customer(customer_db, customer_id) -> None:
    with TestClient(app) as client:
        response = client.post(
            f"/customers/{customer_id}/contracts",
            data={
                "status": "active",
                "plan": "Business",
                "amount": "1,200,000",
                "expires_at": (datetime.now() + timedelta(days=30)).date().isoformat(),
            },
            follow_redirects=False,
        )
    assert response.status_code == 303
    with customer_db() as session:
        profile = session.get(CustomerProfile, customer_id)
        contract = session.query(ContractRecord).one()
        assert profile.customer_state == "service"
        # A contract status is not a board stage. Saving one settles customer_state and
        # leaves the pipeline column where the operator put it — before migration 0040
        # this wrote "active" into pipeline_stage, which is how that string became a
        # board column in the first place.
        assert profile.pipeline_stage == "new"
        assert contract.amount == Decimal("1200000.00")
        assert contract.conversation_id is not None


def test_contract_uses_selected_inquiry_not_latest_contact_inquiry(
    customer_db, customer_id
) -> None:
    with customer_db() as session:
        selected = session.query(Conversation).filter_by(contact_id=customer_id).one()
        selected.stage = "negotiation"
        selected.sheet_client_id = 1336
        latest = Conversation(
            contact_id=customer_id,
            stage="new",
            sheet_client_id=1337,
            created_at=datetime.now() + timedelta(seconds=1),
        )
        session.add(latest)
        session.commit()
        selected_id = selected.id
        latest_id = latest.id

    with TestClient(app) as client:
        response = client.post(
            f"/customers/{customer_id}/contracts",
            data={
                "conversation_id": str(selected_id),
                "status": "contracted",
                "amount": "123.45",
            },
            follow_redirects=False,
        )
        detail = client.get(f"/api/ui/customers/{customer_id}").json()

    assert response.status_code == 303
    saved = detail["contracts"][0]
    assert (saved["amount"], saved["currency"]) == (123.45, "KRW")
    with customer_db() as session:
        contract = session.query(ContractRecord).one()
        assert contract.conversation_id == selected_id
        assert contract.sheet_client_id == 1336
        assert contract.amount == Decimal("123.45")
        # Saving a contract links the inquiry but no longer moves its board stage.
        assert session.get(Conversation, selected_id).stage == "negotiation"
        assert session.get(Conversation, latest_id).stage == "new"


def test_contract_rejects_another_contacts_inquiry(customer_db, customer_id) -> None:
    with customer_db() as session:
        other = Contact(normalized_email="other@example.com", full_name="Other")
        session.add(other)
        session.flush()
        conversation = Conversation(contact_id=other.id, stage="new", sheet_client_id=1400)
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id

    with TestClient(app) as client:
        response = client.post(
            f"/customers/{customer_id}/contracts",
            data={"conversation_id": str(conversation_id), "status": "draft"},
            follow_redirects=False,
        )

    assert response.status_code == 400


def test_operations_surfaces_stale_and_renewal(customer_db, customer_id) -> None:
    with customer_db() as session:
        session.add(CustomerProfile(contact_id=customer_id, customer_state="negotiation"))
        session.add(
            ContractRecord(
                contact_id=customer_id,
                status="active",
                plan="Business",
                expires_at=datetime.now() + timedelta(days=30),
            )
        )
        session.commit()
    with TestClient(app) as client:
        payload = client.get("/api/ui/operations").json()
    assert "Example Co" in [row["company"] for row in payload["lists"]["stale"]]
    assert payload["renewals"], "an active contract expiring inside 60 days must show"


@pytest.mark.parametrize(
    ("silent_days", "expected_bucket"),
    [
        (1, None),                 # inside the grace window — not due yet
        (5, "due_reminder_1"),     # past 3d
        (11, "due_reminder_2"),    # past 3+7d
        (30, "due_unqualified"),   # past 3+7+3d
    ],
)
def test_operations_follow_up_ladder_buckets(
    customer_db, customer_id, silent_days, expected_bucket
) -> None:
    """Each thread lands on exactly one rung of the 3/7/3 ladder, keyed off our last mail."""
    with customer_db() as session:
        conv = session.query(Conversation).filter_by(contact_id=customer_id).one()
        # We mailed last; the customer has been silent since.
        conv.last_outgoing_at = datetime.now() - timedelta(days=silent_days)
        conv.last_incoming_at = datetime.now() - timedelta(days=silent_days + 1)
        session.commit()

    with TestClient(app) as client:
        lists = client.get("/api/ui/operations").json()["lists"]

    buckets = ("due_reminder_1", "due_reminder_2", "due_unqualified")
    populated = [bucket for bucket in buckets if lists[bucket]]
    assert populated == ([expected_bucket] if expected_bucket else [])
    # Never double-counted: a thread appears on at most one rung.
    assert len(populated) <= 1


def test_customer_detail_offers_only_stages_the_board_still_has(customer_id) -> None:
    """The profile stage picker used to carry its own hardcoded copy of the stage list.

    It went stale on the trim and offered contracted/onboarding/active — values the POST
    handler then rejected with a 400, so the form could not be submitted at all. The
    server ships the options now, from the board's own tuple.
    """
    from src.api.web.routes.customer_ops import PIPELINE_STAGES

    with TestClient(app) as client:
        page = client.get(f"/api/ui/customers/{customer_id}").json()
        rejected = client.post(
            f"/customers/{customer_id}/profile",
            data={"customer_state": "negotiation", "pipeline_stage": "onboarding"},
            follow_redirects=False,
        )

    assert [option["key"] for option in page["stage_options"]] == [
        key for key, _, _ in PIPELINE_STAGES
    ]
    for retired in ("contracted", "onboarding", "active", "follow_up_needed"):
        assert retired not in [option["key"] for option in page["stage_options"]]
    assert rejected.status_code == 400


def test_pipeline_board_moves_card_locally(customer_db, customer_id) -> None:
    with customer_db() as session:
        conversation_id = session.query(Conversation).filter_by(contact_id=customer_id).one().id
    with TestClient(app) as client:
        board = client.get("/api/ui/dashboard")
        moved = client.post(
            f"/pipeline/conversations/{conversation_id}/stage",
            data={"stage": "won"},
            follow_redirects=False,
        )
        rejected = client.post(
            f"/pipeline/conversations/{conversation_id}/stage",
            data={"stage": "active"},
            follow_redirects=False,
        )
    assert board.status_code == 200
    assert [stage["key"] for stage in board.json()["stages"]][0] == "new"
    assert moved.status_code == 303
    # A retired stage key must be refused, not silently accepted and then rendered
    # in the New column.
    assert rejected.status_code == 400
    with customer_db() as session:
        profile = session.get(CustomerProfile, customer_id)
        assert profile.pipeline_stage == "won"
        assert profile.customer_state == "service"
        assert session.get(Conversation, conversation_id).stage == "won"


def test_sync_state_separates_blocked_from_failed() -> None:
    """"저장됐다"와 "연동됐다"는 다른 말이다.

    False is attempted-and-failed; None is not attempted at all (no ticket id, no sheet
    row, or pre-launch safe mode). Reporting None as success promised the operator that
    HubSpot and the workbook had moved when nothing had been sent.
    """
    from src.api.web.routes.customer_ops import _sync_state

    assert _sync_state({"sheets": True, "hubspot": True}) == "ok"
    assert _sync_state({"sheets": None, "hubspot": True}) == "ok"
    assert _sync_state({"sheets": False, "hubspot": True}) == "partial"
    assert _sync_state({"sheets": None, "hubspot": False}) == "partial"
    assert _sync_state({"sheets": None, "hubspot": None}) == "local"


def test_board_move_in_safe_mode_reports_local_not_a_failure(
    customer_db, customer_id, monkeypatch
) -> None:
    """Pre-launch, a card move is local-only BY DESIGN, so it must not cry 동기화 실패.

    The HubSpot write raises ExternalWriteBlocked; that is the 대전제 working, and the
    banner has to say "저장했지만 연동은 하지 않았다" rather than warn about a failure.
    """
    from src.common import safe_mode
    from src.common.config import settings

    monkeypatch.setattr(settings, "LIVE_EXTERNAL_WRITES", False)
    monkeypatch.setattr(settings, "HUBSPOT_TICKET_STAGE_WON", "stage-won")
    assert safe_mode.safe_mode() is True

    with customer_db() as session:
        conversation = session.query(Conversation).filter_by(contact_id=customer_id).one()
        conversation.hubspot_ticket_id = "T-900"
        session.commit()
        conversation_id = conversation.id

    with TestClient(app) as client:
        moved = client.post(
            f"/pipeline/conversations/{conversation_id}/stage",
            data={"stage": "won"},
            follow_redirects=False,
        )
    assert moved.status_code == 303
    assert moved.headers["location"] == "/?sync=local#stage-won"
    # The local move still sticks — that is the half that must never depend on HubSpot.
    with customer_db() as session:
        assert session.get(Conversation, conversation_id).stage == "won"


def test_board_move_finds_the_sheet_row_on_the_contact(customer_db, customer_id) -> None:
    """A conversation carries its own sheet id only when THIS app appended the row.

    Rows imported from the workbook put the id on the contact instead, and the board used
    to read the conversation's alone — so a drop for an imported inquiry skipped the Sheet
    while the same move from the customer page updated it.
    """
    from src.api.web.routes.customer_ops import _set_conversation_stage

    with customer_db() as session:
        contact = session.get(Contact, customer_id)
        contact.sheet_client_id = 4321
        conversation = session.query(Conversation).filter_by(contact_id=customer_id).one()
        assert conversation.sheet_client_id is None
        session.commit()
        conversation_id = conversation.id

    _ticket, _contact_id, sheet_client_id = _set_conversation_stage(conversation_id, "negotiation")
    assert sheet_client_id == 4321


def test_the_workbook_has_no_wording_for_two_board_stages() -> None:
    """A KNOWN gap, pinned so it cannot be mistaken for working.

    The board has seven stages; the workbook's Deal Stage column has words for five. Moving
    a card to Reminder Sent or Closed updates this database and HubSpot but leaves the
    sheet on its previous stage (the write logs a warning and reports failure, so it shows
    up as 동기화 실패 rather than silence). Fixing it needs the two values the sales team
    actually uses in that column — invented wording would corrupt their filters. When they
    are added here, delete this test.
    """
    from src.api.web.routes.customer_ops import PIPELINE_STAGES
    from src.integrations.google_sheets import _STAGE_VALUES

    missing = [key for key, _label, _description in PIPELINE_STAGES if key not in _STAGE_VALUES]
    assert missing == ["reminder_sent", "closed"]


def test_pipeline_cards_have_no_stage_dropdown(customer_db, customer_id) -> None:
    """Cards move by drag and drop only.

    The per-card 단계 변경 <select> is gone: it repeated the drop target and took a third
    of the card. The POST it submitted to stays — the drop handler calls it — and so does
    the 파이프라인 select on the customer page, which writes the profile projection.
    """
    with TestClient(app) as client:
        board = client.get("/api/ui/dashboard").json()
    assert any(stage["cards"] for stage in board["stages"])   # the board has cards
    # Dropping is the only way to move a card: no per-card stage <select> anywhere.
    source = pathlib.Path("frontend/src/ui/Board.tsx").read_text(encoding="utf-8")
    assert "<select" not in source
    assert "단계 변경" not in source


def test_pipeline_keeps_each_inquiry_stage_and_only_latest_updates_profile(
    customer_db, customer_id
) -> None:
    with customer_db() as session:
        older = session.query(Conversation).filter_by(contact_id=customer_id).one()
        older.stage = "new"
        older.created_at = datetime.now() - timedelta(days=2)
        newer = Conversation(
            contact_id=customer_id,
            stage="negotiation",
            created_at=datetime.now() - timedelta(days=1),
        )
        session.add(newer)
        session.add(
            CustomerProfile(
                contact_id=customer_id,
                pipeline_stage="negotiation",
                customer_state="negotiation",
            )
        )
        session.commit()
        older_id = older.id
        newer_id = newer.id

    with TestClient(app) as client:
        board = client.get("/api/ui/dashboard")
        moved = client.post(
            f"/pipeline/conversations/{older_id}/stage",
            data={"stage": "closed_lost"},
            follow_redirects=False,
        )

    assert board.status_code == 200
    assert moved.status_code == 303
    with customer_db() as session:
        assert session.get(Conversation, older_id).stage == "closed_lost"
        assert session.get(Conversation, newer_id).stage == "negotiation"
        profile = session.get(CustomerProfile, customer_id)
        assert profile.pipeline_stage == "negotiation"
        assert profile.customer_state == "negotiation"


def test_insights_show_volume_and_country(customer_db, customer_id) -> None:
    with customer_db() as session:
        contact = session.get(Contact, customer_id)
        contact.country = "Korea"
        conversation = session.query(Conversation).filter_by(contact_id=customer_id).one()
        session.add(
            Message(
                conversation_id=conversation.id,
                direction="inbound",
                channel="email",
                body="pricing inquiry",
                status="received",
                score_snapshot=82,
            )
        )
        session.commit()
    with TestClient(app) as client:
        payload = client.get("/api/ui/operations?period=day").json()
    assert payload["period"] == "day"
    assert payload["chart"]                                   # the volume series
    assert "Korea" in [row["country"] for row in payload["country_rows"]]
    assert payload["qualified_count"] >= 0                    # the 70점 이상 counter


def test_contract_can_be_corrected_without_duplicate(customer_db, customer_id) -> None:
    with customer_db() as session:
        contract = ContractRecord(contact_id=customer_id, status="draft", plan="Starter")
        session.add(contract)
        session.commit()
        contract_id = contract.id
    with TestClient(app) as client:
        response = client.post(
            f"/customers/{customer_id}/contracts/{contract_id}",
            data={"status": "sent", "plan": "Business", "amount": "250000", "currency": "KRW"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    with customer_db() as session:
        contracts = session.query(ContractRecord).all()
        assert len(contracts) == 1
        assert contracts[0].plan == "Business"
        assert contracts[0].amount == 250000


def test_customer_operations_migration_creates_tables() -> None:
    import importlib

    migration = importlib.import_module("src.db.migrations.0026_customer_operations")
    engine = create_engine("sqlite:///:memory:")
    migration.up(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"customer_profiles", "customer_interactions", "contract_records"} <= tables


def test_0037_migration_links_only_unambiguous_legacy_contracts() -> None:
    import importlib

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE contacts (id INTEGER PRIMARY KEY)"))
        conn.execute(
            text(
                "CREATE TABLE conversations (id INTEGER PRIMARY KEY, contact_id INTEGER, "
                "sheet_client_id INTEGER)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE contract_records (id INTEGER PRIMARY KEY, contact_id INTEGER, "
                "amount FLOAT)"
            )
        )
        conn.execute(text("INSERT INTO contacts (id) VALUES (1), (2)"))
        conn.execute(
            text(
                "INSERT INTO conversations (id, contact_id, sheet_client_id) VALUES "
                "(10, 1, 1336), (20, 2, 1337), (21, 2, 1338)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO contract_records (id, contact_id, amount) VALUES "
                "(100, 1, 10.25), (200, 2, 20.50)"
            )
        )

    migration = importlib.import_module(
        "src.db.migrations.0037_contract_inquiry_and_decimal"
    )
    migration.up(engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, conversation_id, sheet_client_id FROM contract_records ORDER BY id"
            )
        ).all()
    assert rows == [(100, 10, 1336), (200, None, None)]
    assert isinstance(ContractRecord.__table__.c.amount.type, Numeric)
