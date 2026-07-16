"""Customer history, pipeline, interaction, and contract UI tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db.models import Contact, ContractRecord, Conversation, CustomerInteraction, CustomerProfile


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
        assert contract.amount == 1_200_000


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


def test_customer_operations_migration_creates_tables() -> None:
    import importlib

    migration = importlib.import_module("src.db.migrations.0026_customer_operations")
    engine = create_engine("sqlite:///:memory:")
    migration.up(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"customer_profiles", "customer_interactions", "contract_records"} <= tables
