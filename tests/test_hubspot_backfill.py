"""One-shot HubSpot -> DB backfill of the [B2B] AI Dubbing pipeline.

The inbound pipeline only ingests tickets arriving in the New stage, so a portal
with history renders as a single card. These pin that the backfill fills the board
without ever being able to draft or send.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents import hubspot_backfill
from src.common.config import settings
from src.db.base import Base
from src.db.models import Contact, Conversation, CustomerProfile, Event
from src.integrations.hubspot_models import ContactDTO, TicketDTO

PIPELINE = hubspot_backfill.B2B_PIPELINE_ID
STAGE_IDS = {
    "HUBSPOT_TICKET_STAGE_NEW": "1172180243",
    "HUBSPOT_TICKET_STAGE_AFTER_SEND": "1193842435",
    "HUBSPOT_TICKET_STAGE_NEGOTIATION": "1193733925",
    "HUBSPOT_TICKET_STAGE_WON": "1196772135",
    "HUBSPOT_TICKET_STAGE_CLOSED_LOST": "1172180246",
    "HUBSPOT_TICKET_STAGE_FOLLOW_UP_NEEDED": "1193733926",
}


@pytest.fixture()
def stages(monkeypatch):
    for attr, value in STAGE_IDS.items():
        monkeypatch.setattr(settings, attr, value)


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(hubspot_backfill, "SessionLocal", factory)
    return factory


def _ticket(tid: str, stage: str, subject: str = "문의") -> TicketDTO:
    return TicketDTO(
        id=tid,
        subject=subject,
        pipeline=PIPELINE,
        pipeline_stage=stage,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )


class FakeHubSpot:
    """Stands in for HubSpotClient; records what the backfill asked for."""

    def __init__(self, pairs, contacts):
        self.pairs = pairs
        self.contacts = contacts
        self.asked_pipeline = None
        self.asked_contact_ids = None

    def list_tickets_with_contacts_sync(self, pipeline=None, page_limit=100):
        self.asked_pipeline = pipeline
        return self.pairs

    def get_contacts_batch_sync(self, contact_ids):
        self.asked_contact_ids = list(contact_ids)
        return {c.id: c for c in self.contacts if c.id in set(contact_ids)}


@pytest.fixture()
def fake(monkeypatch, db, stages):
    pairs = [
        (_ticket("t-new", "1172180243"), ["c1"]),
        (_ticket("t-won", "1196772135"), ["c2"]),
        (_ticket("t-lost", "1172180246"), ["c3"]),
        (_ticket("t-followup", "1193733926"), ["c4"]),
        (_ticket("t-orphan", "1193733925"), []),  # no contact on the ticket
    ]
    contacts = [
        ContactDTO(id="c1", email="buyer@bigcorp.com", firstname="Buyer", company="BigCorp"),
        ContactDTO(id="c2", email="won@othercorp.com", firstname="Won"),
        ContactDTO(id="c3", email="lost@gmail.com", firstname="Lost"),
        ContactDTO(id="c4", email=None),  # contact with no email at all
    ]
    client = FakeHubSpot(pairs, contacts)
    monkeypatch.setattr(hubspot_backfill, "HubSpotClient", lambda *a, **k: client)
    return client


def test_backfill_creates_rows_for_every_stage(db, fake):
    counts = hubspot_backfill.backfill_b2b_pipeline()

    assert fake.asked_pipeline == PIPELINE, "must scope the fetch to the B2B pipeline"
    assert counts["tickets"] == 5
    assert counts["conversations_created"] == 4
    assert counts["skipped_no_contact"] == 1  # the ticket with no contact

    with db() as s:
        stages = {c.hubspot_ticket_id: c.stage for c in s.query(Conversation).all()}
    assert stages == {
        "t-new": "new",
        "t-won": "won",
        "t-lost": "closed_lost",
        "t-followup": "follow_up_needed",
    }


def test_backfill_never_creates_messages_or_jobs(db, fake):
    """The whole safety argument: no Message row, no InboundJob row, so neither the
    send worker nor the inbound worker can ever pick these up."""
    from src.db.models import InboundJob, Message

    hubspot_backfill.backfill_b2b_pipeline()

    with db() as s:
        assert s.query(Message).count() == 0
        assert s.query(InboundJob).count() == 0


def test_backfill_leaves_last_incoming_at_null(db, fake):
    """sheet_sync.sync_pending_inbound_rows selects on last_incoming_at IS NOT NULL;
    setting it would queue every backfilled row for the shared workbook."""
    hubspot_backfill.backfill_b2b_pipeline()
    with db() as s:
        assert all(c.last_incoming_at is None for c in s.query(Conversation).all())
        assert all(c.sheet_client_id is None for c in s.query(Conversation).all())


def test_backfill_rows_are_invisible_to_the_sheet_sync(db, fake, monkeypatch):
    """The bulk import must never end up appended to the shared sales workbook.

    sync_pending_inbound_rows selects `sheet_inbound_row IS NULL AND
    last_incoming_at IS NOT NULL`; backfilled rows fail the second half, so they are
    not queued even after LIVE_EXTERNAL_WRITES is turned on.
    """
    from sqlalchemy import select

    from src.agents import sheet_sync

    hubspot_backfill.backfill_b2b_pipeline()

    monkeypatch.setattr(sheet_sync, "SessionLocal", db)
    with db() as s:
        queued = s.execute(
            select(Conversation.id).where(
                Conversation.sheet_inbound_row.is_(None),
                Conversation.last_incoming_at.isnot(None),
            )
        ).all()
    assert queued == [], "backfilled conversations must not be queued for the sheet"

    # And the operator-facing "미처리" badge stays at zero because of it.
    assert sheet_sync.pending_inbound_count() == 0


def test_backfill_is_idempotent(db, fake):
    first = hubspot_backfill.backfill_b2b_pipeline()
    second = hubspot_backfill.backfill_b2b_pipeline()

    assert first["conversations_created"] == 4
    assert second["conversations_created"] == 0
    assert second["contacts_created"] == 0
    with db() as s:
        assert s.query(Conversation).count() == 4
        assert s.query(Contact).count() == 4


def test_backfill_reruns_pick_up_a_stage_move(db, fake):
    hubspot_backfill.backfill_b2b_pipeline()
    fake.pairs[0] = (_ticket("t-new", "1193733925"), ["c1"])  # New -> Negotiating

    counts = hubspot_backfill.backfill_b2b_pipeline()

    assert counts["conversations_updated"] == 1
    with db() as s:
        conv = s.query(Conversation).filter_by(hubspot_ticket_id="t-new").one()
        assert conv.stage == "negotiation"
        assert s.get(CustomerProfile, conv.contact_id).pipeline_stage == "negotiation"


def test_personal_domains_are_not_grouped_as_a_company(db, fake):
    """Project invariant: a gmail sender must not get a company domain."""
    hubspot_backfill.backfill_b2b_pipeline()
    with db() as s:
        personal = s.query(Contact).filter_by(normalized_email="lost@gmail.com").one()
        corporate = s.query(Contact).filter_by(normalized_email="buyer@bigcorp.com").one()
    assert personal.domain is None
    assert corporate.domain == "bigcorp.com"


def test_contact_without_email_still_gets_a_unique_identity(db, fake):
    """normalized_email and full_name are NOT NULL; a blank one would break the insert."""
    hubspot_backfill.backfill_b2b_pipeline()
    with db() as s:
        c = s.query(Contact).filter(Contact.normalized_email.like("unknown:%")).one()
    assert c.normalized_email == "unknown:hs-c4"
    assert c.full_name  # non-empty


def test_request_and_status_round_trip(db, fake):
    request_id = hubspot_backfill.request_hubspot_backfill("tester")
    status = hubspot_backfill.hubspot_backfill_status()
    assert status["status"] == "requested" and status["actor"] == "tester"

    assert hubspot_backfill.process_requested_hubspot_backfill() is True

    status = hubspot_backfill.hubspot_backfill_status()
    assert status["status"] == "completed"
    assert status["request_id"] == request_id
    assert status["conversations_created"] == 4

    # Exactly one request is consumed per call.
    assert hubspot_backfill.process_requested_hubspot_backfill() is False

    with db() as s:
        kinds = [e.kind for e in s.query(Event).order_by(Event.id).all()]
    assert kinds == [
        hubspot_backfill.BACKFILL_REQUESTED,
        hubspot_backfill.BACKFILL_STARTED,
        hubspot_backfill.BACKFILL_COMPLETED,
    ]


def test_failure_is_recorded_and_retryable(db, monkeypatch, stages):
    def boom(*a, **k):
        raise RuntimeError("HubSpot exploded")

    monkeypatch.setattr(hubspot_backfill, "backfill_b2b_pipeline", boom)
    hubspot_backfill.request_hubspot_backfill("tester")

    assert hubspot_backfill.process_requested_hubspot_backfill() is False
    status = hubspot_backfill.hubspot_backfill_status()
    assert status["status"] == "failed" and "exploded" in status["error"]

    # A failed request is terminal, so it is not retried in a loop.
    assert hubspot_backfill.process_requested_hubspot_backfill() is False
