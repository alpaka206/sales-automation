"""Tests for importing the existing sales ledger into local customer history."""

from __future__ import annotations

import importlib
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.agents import sheet_sync
from src.db.models import Contact, ContractRecord, Conversation, CustomerProfile, Event, Message


def test_import_inbound_history_is_idempotent_and_keeps_inquiries_separate(
    monkeypatch, db_session_factory
):
    rows = [
        {
            "client_id": "1336",
            "inquiry_date": "2026. 07. 01.",
            "deal_stage": "New",
            "company": "Example Co",
            "full_name": "Kim",
            "email": "buyer@example.com",
            "country": "KR",
            "history": "First inquiry",
            "_row": 2,
        },
        {
            "client_id": "1337",
            "inquiry_date": "2026-07-18",
            "deal_stage": "Negotiation",
            "company": "Example Co",
            "full_name": "Kim",
            "email": "buyer@example.com",
            "country": "KR",
            "history": "Second inquiry",
            "_row": 3,
        },
    ]
    monkeypatch.setattr(sheet_sync, "SessionLocal", db_session_factory)
    monkeypatch.setattr(sheet_sync, "read_inbound_records", lambda limit=5000: rows)

    assert sheet_sync.import_inbound_history() == 2
    assert sheet_sync.import_inbound_history() == 2

    with db_session_factory() as session:
        assert session.scalar(select(func.count(Contact.id))) == 1
        assert session.scalar(select(func.count(Conversation.id))) == 2
        assert session.scalar(select(func.count(Message.id))) == 2
        conversations = session.scalars(select(Conversation).order_by(Conversation.sheet_client_id)).all()
        assert [(row.sheet_client_id, row.sheet_inbound_row, row.stage) for row in conversations] == [
            (1336, 2, "new"),
            (1337, 3, "negotiation"),
        ]


def test_manual_sheet_sync_request_is_durable_and_reports_completion(
    monkeypatch, db_session_factory
):
    monkeypatch.setattr(sheet_sync, "SessionLocal", db_session_factory)
    monkeypatch.setattr(sheet_sync, "import_inbound_history", lambda limit=5000: 7)
    monkeypatch.setattr(sheet_sync, "sync_pending_inbound_rows", lambda limit=200: 2)
    monkeypatch.setattr(sheet_sync, "sync_pending_order_rows", lambda limit=200: 1)

    request_id = sheet_sync.request_full_sheet_sync("operator@example.com")
    queued = sheet_sync.full_sheet_sync_status()
    assert queued["request_id"] == request_id
    assert queued["status"] == "requested"

    assert sheet_sync.process_requested_sheet_sync()
    assert not sheet_sync.process_requested_sheet_sync()
    completed = sheet_sync.full_sheet_sync_status()
    assert completed["status"] == "completed"
    assert completed["imported"] == 7
    assert completed["inbound"] == 2
    assert completed["orders"] == 1

    with db_session_factory() as session:
        kinds = session.scalars(select(Event.kind).order_by(Event.id)).all()
    assert kinds == [
        sheet_sync.SHEET_SYNC_REQUESTED,
        sheet_sync.SHEET_SYNC_STARTED,
        sheet_sync.SHEET_SYNC_COMPLETED,
    ]


def test_manual_sheet_sync_failure_is_visible_and_can_be_requested_again(
    monkeypatch, db_session_factory
):
    monkeypatch.setattr(sheet_sync, "SessionLocal", db_session_factory)

    def fail(_limit=5000):
        raise RuntimeError("Google unavailable")

    monkeypatch.setattr(sheet_sync, "import_inbound_history", fail)
    first = sheet_sync.request_full_sheet_sync("operator")
    assert not sheet_sync.process_requested_sheet_sync()
    failed = sheet_sync.full_sheet_sync_status()
    assert failed["request_id"] == first
    assert failed["status"] == "failed"
    assert "Google unavailable" in failed["error"]

    second = sheet_sync.request_full_sheet_sync("operator")
    assert second != first
    assert sheet_sync.full_sheet_sync_status()["status"] == "requested"


@pytest.mark.parametrize(
    "value",
    ("2026-07-18", "2026. 7. 18.", "2026.7.18", "2026/7/18", "7/18/2026"),
)
def test_sheet_date_accepts_real_google_display_formats(value):
    assert sheet_sync._sheet_date(value).date().isoformat() == "2026-07-18"


def test_import_normalizes_plus_email_and_promotes_placeholder(monkeypatch, db_session_factory):
    current_rows = [{"client_id": "1336", "deal_stage": "New", "_row": 2}]
    monkeypatch.setattr(sheet_sync, "SessionLocal", db_session_factory)
    monkeypatch.setattr(
        sheet_sync, "read_inbound_records", lambda limit=5000: list(current_rows)
    )
    sheet_sync.import_inbound_history()

    current_rows[:] = [
        {
            "client_id": "1336",
            "deal_stage": "New",
            "email": "Buyer+Sales@Example.com",
            "_row": 2,
        },
        {
            "client_id": "1337",
            "deal_stage": "Negotiation",
            "email": "buyer@example.com",
            "_row": 3,
        },
    ]
    sheet_sync.import_inbound_history()

    with db_session_factory() as session:
        contacts = session.scalars(select(Contact)).all()
        conversations = session.scalars(select(Conversation)).all()
        assert len(contacts) == 1
        assert contacts[0].normalized_email == "buyer@example.com"
        assert len(conversations) == 2
        assert {item.contact_id for item in conversations} == {contacts[0].id}


def test_unknown_stage_preserves_existing_stage_and_backfills_inquiry_time(
    monkeypatch, db_session_factory
):
    with db_session_factory() as session:
        contact = Contact(
            normalized_email="buyer@example.com",
            email="buyer@example.com",
            full_name="Kim",
            sheet_client_id=1336,
        )
        session.add(contact)
        session.flush()
        session.add(
            Conversation(
                contact_id=contact.id,
                stage="negotiation",
                sheet_client_id=1336,
                last_incoming_at=None,
            )
        )
        session.add(
            CustomerProfile(
                contact_id=contact.id,
                pipeline_stage="negotiation",
                customer_state="negotiation",
            )
        )
        session.commit()

    monkeypatch.setattr(sheet_sync, "SessionLocal", db_session_factory)
    monkeypatch.setattr(
        sheet_sync,
        "read_inbound_records",
        lambda limit=5000: [
            {
                "client_id": "1336",
                "email": "buyer@example.com",
                "inquiry_date": "2026/07/18",
                "deal_stage": "Paused by customer",
                "_row": 2,
            }
        ],
    )
    sheet_sync.import_inbound_history()

    with db_session_factory() as session:
        conversation = session.scalar(select(Conversation))
        profile = session.scalar(select(CustomerProfile))
        assert conversation.stage == "negotiation"
        assert conversation.last_incoming_at.date().isoformat() == "2026-07-18"
        assert profile.pipeline_stage == "negotiation"
        assert profile.customer_state == "negotiation"


def test_profile_uses_latest_inquiry_not_physical_row_order(monkeypatch, db_session_factory):
    rows = [
        {
            "client_id": "1337",
            "email": "buyer@example.com",
            "inquiry_date": "2026-07-18",
            "deal_stage": "Won",
            "plan": "Enterprise",
            "_row": 2,
        },
        {
            "client_id": "1336",
            "email": "buyer@example.com",
            "inquiry_date": "2026-01-01",
            "deal_stage": "New",
            "plan": "Trial",
            "_row": 3,
        },
    ]
    calls = 0

    def session_factory():
        nonlocal calls
        calls += 1
        return db_session_factory()

    monkeypatch.setattr(sheet_sync, "SessionLocal", session_factory)
    monkeypatch.setattr(sheet_sync, "read_inbound_records", lambda limit=5000: rows)

    sheet_sync.import_inbound_history()

    assert calls == 1
    with db_session_factory() as session:
        profile = session.scalar(select(CustomerProfile))
        # Workbook "Won" -> local "won". It used to import as "contracted", a key the
        # board no longer has; the round trip now uses the same name in both directions.
        assert profile.pipeline_stage == "won"
        assert profile.customer_state == "service"
        assert profile.current_plan == "Enterprise"


def test_import_retries_once_after_concurrent_unique_race(monkeypatch):
    calls = 0

    def import_records(_records):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise IntegrityError("INSERT", {}, RuntimeError("unique race"))
        return 1

    monkeypatch.setattr(sheet_sync, "read_inbound_records", lambda limit=5000: [{}])
    monkeypatch.setattr(sheet_sync, "_import_inbound_records", import_records)

    assert sheet_sync.import_inbound_history() == 1
    assert calls == 2


def test_pending_sync_reuses_the_contacts_company_client_id(
    monkeypatch, db_session_factory
):
    with db_session_factory() as session:
        contact = Contact(
            normalized_email="buyer@example.com",
            email="buyer@example.com",
            full_name="Kim",
            sheet_client_id=1336,
        )
        session.add(contact)
        session.flush()
        conversation = Conversation(
            contact_id=contact.id,
            stage="new",
            sheet_client_id=1337,
            last_incoming_at=datetime(2026, 7, 18),
        )
        session.add(conversation)
        session.flush()
        session.add(
            Message(
                conversation_id=conversation.id,
                direction="inbound",
                body="Hello",
                status="received",
                created_at=datetime(2026, 7, 18),
            )
        )
        session.commit()

    captured = {}

    def record_inbound(record):
        captured.update(record)
        return SimpleNamespace(row=42, client_id=record["client_id"])

    monkeypatch.setattr(sheet_sync, "SessionLocal", db_session_factory)
    monkeypatch.setattr(sheet_sync, "writes_enabled", lambda: True)
    monkeypatch.setattr(sheet_sync, "record_inbound", record_inbound)

    assert sheet_sync.sync_pending_inbound_rows() == 1
    assert captured["client_id"] == 1336
    assert captured["inquiry_key"] == "conversation:1"


def test_order_sync_uses_contract_inquiry_snapshot(monkeypatch, db_session_factory):
    with db_session_factory() as session:
        contact = Contact(
            normalized_email="order@example.com",
            email="order@example.com",
            full_name="Order User",
            sheet_client_id=1336,
        )
        session.add(contact)
        session.flush()
        conversation = Conversation(
            contact_id=contact.id,
            stage="won",
            sheet_client_id=1337,
        )
        session.add(conversation)
        session.flush()
        contract = ContractRecord(
            contact_id=contact.id,
            conversation_id=conversation.id,
            sheet_client_id=conversation.sheet_client_id,
            status="contracted",
            amount="123.45",
        )
        session.add(contract)
        session.commit()
        contract_id = contract.id

    captured = {}

    def record_order(record):
        captured.update(record)
        return SimpleNamespace(row=22)

    monkeypatch.setattr(sheet_sync, "SessionLocal", db_session_factory)
    monkeypatch.setattr(sheet_sync, "writes_enabled", lambda: True)
    monkeypatch.setattr(sheet_sync, "record_order", record_order)

    assert sheet_sync.sync_contract_order(contract_id)
    assert captured["client_id"] == 1337
    assert captured["amount"] == 123.45


def test_order_sync_hydrates_snapshot_after_inquiry_sync(monkeypatch, db_session_factory):
    with db_session_factory() as session:
        contact = Contact(normalized_email="late-order@example.com", full_name="Late")
        session.add(contact)
        session.flush()
        conversation = Conversation(contact_id=contact.id, stage="won", sheet_client_id=1450)
        session.add(conversation)
        session.flush()
        contract = ContractRecord(
            contact_id=contact.id,
            conversation_id=conversation.id,
            sheet_client_id=None,
            status="contracted",
        )
        session.add(contract)
        session.commit()
        contract_id = contract.id

    monkeypatch.setattr(sheet_sync, "SessionLocal", db_session_factory)
    monkeypatch.setattr(sheet_sync, "writes_enabled", lambda: True)
    monkeypatch.setattr(
        sheet_sync, "record_order", lambda record: SimpleNamespace(row=23)
    )

    assert sheet_sync.sync_contract_order(contract_id)
    with db_session_factory() as session:
        assert session.get(ContractRecord, contract_id).sheet_client_id == 1450


def test_reserve_inbound_client_id_uses_sheet_and_local_max(
    monkeypatch, db_session_factory
):
    with db_session_factory() as session:
        contact = Contact(normalized_email="buyer@example.com", full_name="Kim")
        session.add(contact)
        session.flush()
        first = Conversation(contact_id=contact.id, stage="new", sheet_client_id=1450)
        second = Conversation(contact_id=contact.id, stage="new")
        session.add_all([first, second])
        session.commit()
        second_id = second.id

    monkeypatch.setattr(sheet_sync, "SessionLocal", db_session_factory)
    monkeypatch.setattr(sheet_sync, "writes_enabled", lambda: True)
    monkeypatch.setattr(sheet_sync, "suggest_inbound_client_id", lambda: 1400)

    assert sheet_sync.reserve_inbound_client_id(second_id) == 1450
    assert sheet_sync.reserve_inbound_client_id(second_id) == 1450

    with db_session_factory() as session:
        second = session.get(Conversation, second_id)
        assert second.sheet_client_id == 1450
        assert second.sheet_inquiry_key == f"conversation:{second_id}"


def test_reserve_inbound_client_id_reuses_corporate_domain(
    monkeypatch, db_session_factory
):
    with db_session_factory() as session:
        first = Contact(
            normalized_email="first@acme.com",
            email="first@acme.com",
            domain="acme.com",
            full_name="First",
            sheet_client_id=1450,
        )
        second = Contact(
            normalized_email="second@acme.com",
            email="second@acme.com",
            domain="acme.com",
            full_name="Second",
        )
        session.add_all([first, second])
        session.flush()
        conversation = Conversation(contact_id=second.id, stage="new")
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id
        second_id = second.id

    monkeypatch.setattr(sheet_sync, "SessionLocal", db_session_factory)
    monkeypatch.setattr(sheet_sync, "writes_enabled", lambda: True)

    assert sheet_sync.reserve_inbound_client_id(conversation_id) == 1450
    with db_session_factory() as session:
        assert session.get(Contact, second_id).sheet_client_id == 1450
        assert session.get(Conversation, conversation_id).sheet_client_id == 1450


def test_0035_migration_clears_legacy_duplicates_and_enforces_unique_key(db_engine):
    with Session(db_engine) as session:
        contact = Contact(normalized_email="buyer@example.com", full_name="Kim")
        session.add(contact)
        session.flush()
        session.add_all(
            [
                Conversation(contact_id=contact.id, stage="new", sheet_client_id=1336),
                Conversation(contact_id=contact.id, stage="new", sheet_client_id=1336),
            ]
        )
        session.commit()

    migration = importlib.import_module(
        "src.db.migrations.0035_unique_inquiry_sheet_client_id"
    )
    migration.up(db_engine)

    with Session(db_engine) as session:
        values = session.scalars(
            select(Conversation.sheet_client_id).order_by(Conversation.id)
        ).all()
        assert values == [1336, None]
        contact_id = session.scalar(select(Contact.id))
        session.add(Conversation(contact_id=contact_id, stage="new", sheet_client_id=1336))
        with pytest.raises(IntegrityError):
            session.commit()


def test_0085_migration_shares_client_id_but_keeps_inquiry_key_unique(db_engine):
    with Session(db_engine) as session:
        contact = Contact(normalized_email="buyer@acme.com", full_name="Kim")
        session.add(contact)
        session.flush()
        first = Conversation(
            contact_id=contact.id,
            stage="new",
            hubspot_ticket_id="T-1336",
            sheet_client_id=1336,
        )
        session.add(first)
        session.commit()
        contact_id = contact.id
        first_id = first.id
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX ux_conversations_sheet_client_id "
                "ON conversations (sheet_client_id)"
            )
        )

    importlib.import_module(
        "src.db.migrations.0085_company_client_id_and_inquiry_key"
    ).up(db_engine)

    with Session(db_engine) as session:
        assert session.get(Conversation, first_id).sheet_inquiry_key == "hubspot:T-1336"
        session.add(
            Conversation(
                contact_id=contact_id,
                stage="new",
                sheet_client_id=1336,
                sheet_inquiry_key="conversation:second",
            )
        )
        session.commit()
        session.add(
            Conversation(
                contact_id=contact_id,
                stage="new",
                sheet_client_id=1336,
                sheet_inquiry_key="conversation:second",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_the_workbook_never_overrides_a_stage_hubspot_owns(monkeypatch, db_session_factory):
    """티켓이 있는 문의의 단계는 HubSpot 이 정합니다.

    워크북 셀은 사람이 손으로 고치는 값이라 늦기 쉬운데, 예전에는 시간 비교 없이 마지막에
    쓴 쪽이 이겼습니다 — HubSpot 에서 Qualified 로 옮겨 놓아도 다음 전체 동기화가 시트의 옛
    'Negotiation' 으로 되돌렸고, 화면에서는 「HubSpot 변경이 반영 안 됨」과 똑같이 보였습니다.
    """
    with db_session_factory() as session:
        contact = Contact(
            normalized_email="ticketed@example.com",
            email="ticketed@example.com",
            full_name="Kim",
            sheet_client_id=1400,
        )
        session.add(contact)
        session.flush()
        session.add(
            Conversation(
                contact_id=contact.id,
                stage="meeting_link_sent",          # HubSpot 이 방금 옮긴 값
                sheet_client_id=1400,
                hubspot_ticket_id="T-1400",
            )
        )
        session.commit()

    monkeypatch.setattr(sheet_sync, "SessionLocal", db_session_factory)
    monkeypatch.setattr(
        sheet_sync,
        "read_inbound_records",
        lambda limit=5000: [
            {"client_id": "1400", "email": "ticketed@example.com",
             "deal_stage": "Negotiation", "_row": 2},   # 시트의 뒤처진 값
        ],
    )
    sheet_sync.import_inbound_history()

    with db_session_factory() as session:
        assert session.scalar(select(Conversation)).stage == "meeting_link_sent"


def test_the_workbook_still_owns_a_thread_hubspot_never_saw(monkeypatch, db_session_factory):
    """티켓이 없는 행은 여전히 시트가 원본입니다 — 워크북에서만 사는 문의가 있습니다."""
    with db_session_factory() as session:
        contact = Contact(
            normalized_email="sheetonly@example.com",
            email="sheetonly@example.com",
            full_name="Park",
            sheet_client_id=1401,
        )
        session.add(contact)
        session.flush()
        session.add(Conversation(contact_id=contact.id, stage="new", sheet_client_id=1401))
        session.commit()

    monkeypatch.setattr(sheet_sync, "SessionLocal", db_session_factory)
    monkeypatch.setattr(
        sheet_sync,
        "read_inbound_records",
        lambda limit=5000: [
            {"client_id": "1401", "email": "sheetonly@example.com",
             "deal_stage": "Negotiation", "_row": 2},
        ],
    )
    sheet_sync.import_inbound_history()

    with db_session_factory() as session:
        assert session.scalar(select(Conversation)).stage == "negotiation"


def test_a_new_workbook_row_carries_the_stage_the_inquiry_is_actually_in():
    """append 경로가 단계를 "New" 로 박고 있었습니다.

    몇 주째 협상 중인 문의도 워크북에는 New 로 들어갔고, 전체 동기화가 그 값을 우리 DB 로
    되돌려 단계를 잃었습니다. 표는 갱신 경로와 **같은 한 곳**을 씁니다 — 두 경로가 다른 말을
    쓰면 같은 문의가 시트에서 두 얼굴을 갖습니다.
    """
    assert sheet_sync._stage_words("negotiation") == ("Negotiation", "Meeting")
    assert sheet_sync._stage_words("won") == ("Won", "Closed Won")
    assert sheet_sync._stage_words(None) == ("New", "Inquiry")
    # 시트에 아직 말이 없는 단계는 행을 못 만드는 것보다 New 로 두는 편이 낫습니다.
    # 갱신 경로가 그때 경고를 남깁니다(`google_sheets.update_inbound_stage`).
    assert sheet_sync._stage_words("reminder_sent") == ("New", "Inquiry")
