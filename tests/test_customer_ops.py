"""Customer history, pipeline, interaction, and contract UI tests."""

from __future__ import annotations

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
            )
        )
        session.commit()
        return contact.id


def test_customer_list_and_detail(customer_db, customer_id) -> None:
    with TestClient(app) as client:
        listing = client.get("/customers")
        detail = client.get(f"/customers/{customer_id}")
    assert listing.status_code == 200
    assert "Example Co" in listing.text
    assert detail.status_code == 200
    assert "통합 히스토리" in detail.text
    assert "계약이 성사된 문의" in detail.text


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
        assert profile.pipeline_stage == "active"
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
        detail = client.get(f"/customers/{customer_id}")

    assert response.status_code == 303
    assert "123.45 KRW" in detail.text
    with customer_db() as session:
        contract = session.query(ContractRecord).one()
        assert contract.conversation_id == selected_id
        assert contract.sheet_client_id == 1336
        assert contract.amount == Decimal("123.45")
        assert session.get(Conversation, selected_id).stage == "contracted"
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
        response = client.get("/operations")
    assert response.status_code == 200
    assert "14일 이상 소통 없음" in response.text
    assert "Example Co" in response.text


def test_pipeline_board_moves_card_locally(customer_db, customer_id) -> None:
    with customer_db() as session:
        conversation_id = session.query(Conversation).filter_by(contact_id=customer_id).one().id
    with TestClient(app) as client:
        board = client.get("/pipeline")
        moved = client.post(
            f"/pipeline/conversations/{conversation_id}/stage",
            data={"stage": "active"},
            follow_redirects=False,
        )
    assert board.status_code == 200
    assert "문의 파이프라인" in board.text
    assert moved.status_code == 303
    with customer_db() as session:
        profile = session.get(CustomerProfile, customer_id)
        assert profile.pipeline_stage == "active"
        assert profile.customer_state == "service"
        assert session.get(Conversation, conversation_id).stage == "active"


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
        board = client.get("/pipeline")
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
        response = client.get("/operations?period=day")
    assert response.status_code == 200
    assert "문의량 · 누적 추이" in response.text
    assert "Korea" in response.text
    assert "70점 이상" in response.text


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
