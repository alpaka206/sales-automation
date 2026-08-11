"""HubSpot -> local stage detection.

Sales moves tickets in HubSpot directly. Before this existed the webhook accepted a
stage change only when the new value was the New stage and dropped every other
transition, so a ticket could reach Won in HubSpot while our board still showed New.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents import stage_sync
from src.common.config import settings
from src.db.base import Base
from src.db.models import Contact, Conversation, CustomerProfile

# Numeric: HubSpotWebhookEvent.objectId is an int, and the webhook stringifies it.
TICKET = "4200001"

STAGE_IDS = {
    "HUBSPOT_TICKET_STAGE_NEW": "1172180243",
    "HUBSPOT_TICKET_STAGE_AFTER_SEND": "1193842435",
    "HUBSPOT_TICKET_STAGE_NEGOTIATION": "1193733925",
    "HUBSPOT_TICKET_STAGE_REMINDER_SENT": "1196621584",
    "HUBSPOT_TICKET_STAGE_WON": "1196772135",
    "HUBSPOT_TICKET_STAGE_CLOSED_LOST": "1172180246",
    "HUBSPOT_TICKET_STAGE_CLOSED": "1404814097",
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
    monkeypatch.setattr(stage_sync, "SessionLocal", factory)
    with factory() as session:
        contact = Contact(normalized_email="buyer@example.com", full_name="Buyer")
        session.add(contact)
        session.flush()
        session.add(
            Conversation(contact_id=contact.id, stage="new", hubspot_ticket_id=TICKET)
        )
        session.commit()
    return factory


def test_every_pipeline_stage_is_mapped(stages):
    """All 7 stages of [B2B] AI Dubbing must resolve — an unmapped one is invisible."""
    expected = {
        "1172180243": "new",
        "1193842435": "meeting_link_sent",
        "1193733925": "negotiation",
        "1196621584": "reminder_sent",
        "1196772135": "won",
        "1172180246": "closed_lost",
        "1404814097": "closed",
    }
    assert stage_sync.stage_id_to_local() == expected


def test_board_columns_are_exactly_the_seven_stages_in_flow_order():
    """The board's column order IS this tuple — nothing else defines it."""
    from src.api.routes.customer_ops import PIPELINE_STAGES

    assert [key for key, _, _ in PIPELINE_STAGES] == [
        "new",
        "meeting_link_sent",
        "negotiation",
        "reminder_sent",
        "won",
        "closed_lost",
        "closed",
    ]
    assert [label for _, label, _ in PIPELINE_STAGES] == [
        "New",
        "Meeting Link Sent",
        "Negotiating",
        "Reminder Sent",
        "Won",
        "Lost",
        "Closed",
    ]


def test_board_and_hubspot_maps_hold_the_same_keys():
    """A key in one but not the other is a column that cannot sync, or a silent drop."""
    from src.api.routes.customer_ops import VALID_PIPELINE_STAGES

    assert set(stage_sync.LOCAL_STAGE_TO_SETTING) == VALID_PIPELINE_STAGES


def test_the_workbook_round_trip_uses_one_vocabulary():
    """Sheet write and sheet read must agree, or an import undoes the board.

    google_sheets writes ("Won", …) for a local stage; sheet_sync reads it back. When
    the two disagree — as they did while the write said "contracted" and the board said
    "won" — a full sheet sync silently rewrites the stage an operator just set.
    """
    from src.agents.sheet_sync import _local_stage
    from src.api.routes.customer_ops import VALID_PIPELINE_STAGES
    from src.integrations.google_sheets import _STAGE_VALUES

    assert set(_STAGE_VALUES) <= VALID_PIPELINE_STAGES
    for stage, (deal_stage, _detail) in _STAGE_VALUES.items():
        assert _local_stage({"deal_stage": deal_stage}) == stage, deal_stage


def test_settled_states_only_name_stages_that_exist():
    from src.api.routes.customer_ops import VALID_PIPELINE_STAGES

    assert set(stage_sync.STATE_FOR_STAGE) <= VALID_PIPELINE_STAGES


def test_reopening_from_a_settled_stage_returns_to_negotiation():
    assert stage_sync.customer_state_for("won", "negotiation") == "service"
    assert stage_sync.customer_state_for("closed", "service") == "lost"
    assert stage_sync.customer_state_for("negotiation", "lost") == "negotiation"
    assert stage_sync.customer_state_for("new", "prospect") == "prospect"


def test_blank_stage_ids_do_not_collide(monkeypatch):
    """Unconfigured stages must be skipped, not all collapse onto the empty id."""
    for attr in STAGE_IDS:
        monkeypatch.setattr(settings, attr, "")
    assert stage_sync.stage_id_to_local() == {}
    assert stage_sync.local_stage_for("") is None


@pytest.mark.parametrize(
    ("stage_id", "expected_stage", "expected_state"),
    [
        ("1193733925", "negotiation", "negotiation"),
        ("1196621584", "reminder_sent", None),
        ("1196772135", "won", "service"),
        ("1172180246", "closed_lost", "lost"),
        ("1404814097", "closed", "lost"),
    ],
)
def test_hubspot_move_updates_local_conversation(
    db, stages, stage_id, expected_stage, expected_state
):
    assert stage_sync.sync_stage_from_hubspot(TICKET, stage_id) == expected_stage

    with db() as session:
        conv = session.query(Conversation).filter_by(hubspot_ticket_id=TICKET).one()
        assert conv.stage == expected_stage
        profile = session.get(CustomerProfile, conv.contact_id)
        assert profile is not None
        assert profile.pipeline_stage == expected_stage
        if expected_state:
            assert profile.customer_state == expected_state


def test_repeat_of_the_same_stage_is_a_no_op(db, stages):
    """Returns None the second time so callers do not log a phantom transition."""
    assert stage_sync.sync_stage_from_hubspot(TICKET, "1196772135") == "won"
    assert stage_sync.sync_stage_from_hubspot(TICKET, "1196772135") is None


def test_hubspot_move_is_mirrored_into_the_sheet(db, stages, monkeypatch):
    """A stage dragged in HubSpot must reach the sales workbook with no manual step."""
    with db() as session:
        conv = session.query(Conversation).filter_by(hubspot_ticket_id=TICKET).one()
        conv.sheet_client_id = 1042
        session.commit()

    calls: list[tuple] = []
    import src.integrations.google_sheets as gs

    monkeypatch.setattr(
        gs, "update_inbound_stage", lambda cid, stage, *a, **k: calls.append((cid, stage)) or True
    )

    stage_sync.sync_stage_from_hubspot(TICKET, "1196772135")
    assert calls == [(1042, "won")]


def test_sheet_mirror_failure_does_not_break_the_local_move(db, stages, monkeypatch):
    """A Sheets outage must not make the webhook 500 (HubSpot would redeliver)."""
    with db() as session:
        conv = session.query(Conversation).filter_by(hubspot_ticket_id=TICKET).one()
        conv.sheet_client_id = 1042
        session.commit()

    import src.integrations.google_sheets as gs

    def boom(*a, **k):
        raise RuntimeError("Sheets down")

    monkeypatch.setattr(gs, "update_inbound_stage", boom)

    assert stage_sync.sync_stage_from_hubspot(TICKET, "1196772135") == "won"
    with db() as session:
        assert session.query(Conversation).filter_by(hubspot_ticket_id=TICKET).one().stage == "won"


def test_no_sheet_write_without_a_workbook_row(db, stages, monkeypatch):
    """Backfilled conversations have no sheet_client_id, so the bulk import can
    never push hundreds of rows into the shared workbook."""
    calls: list[tuple] = []
    import src.integrations.google_sheets as gs

    monkeypatch.setattr(
        gs, "update_inbound_stage", lambda cid, stage, *a, **k: calls.append((cid, stage)) or True
    )

    stage_sync.sync_stage_from_hubspot(TICKET, "1196772135")  # sheet_client_id is None
    assert calls == []


def test_reopening_clears_a_closed_customer_state(db, stages):
    """Won -> Negotiating must not leave the profile stuck in 'service'."""
    stage_sync.sync_stage_from_hubspot(TICKET, "1196772135")
    stage_sync.sync_stage_from_hubspot(TICKET, "1193733925")
    with db() as session:
        conv = session.query(Conversation).filter_by(hubspot_ticket_id=TICKET).one()
        assert session.get(CustomerProfile, conv.contact_id).customer_state == "negotiation"


def test_unknown_ticket_and_unmapped_stage_are_ignored(db, stages):
    assert stage_sync.sync_stage_from_hubspot("no-such-ticket", "1196772135") is None
    assert stage_sync.sync_stage_from_hubspot(TICKET, "999999999") is None
    assert stage_sync.sync_stage_from_hubspot(None, "1196772135") is None
    with db() as session:
        assert session.query(Conversation).filter_by(hubspot_ticket_id=TICKET).one().stage == "new"


def test_webhook_records_a_non_new_stage_change(db, stages, monkeypatch):
    """The regression: a move to Won used to be dropped as 'ignored'."""
    from src.api import webhook
    from src.api.schemas import HubSpotWebhookEvent

    event = HubSpotWebhookEvent(
        subscriptionType="ticket.propertyChange",
        objectId=TICKET,
        propertyName="hs_pipeline_stage",
        propertyValue="1196772135",
    )
    # Not inbound work...
    assert webhook._map_hubspot_event(event) is None
    # ...but still recorded.
    assert webhook._sync_stage_change(event) == "won"

    with db() as session:
        assert session.query(Conversation).filter_by(hubspot_ticket_id=TICKET).one().stage == "won"


def test_webhook_ignores_non_stage_property_changes(db, stages):
    from src.api import webhook
    from src.api.schemas import HubSpotWebhookEvent

    event = HubSpotWebhookEvent(
        subscriptionType="ticket.propertyChange",
        objectId=TICKET,
        propertyName="subject",
        propertyValue="renamed",
    )
    assert webhook._sync_stage_change(event) is None


def test_poller_reconcile_sweeps_every_stage(db, stages, monkeypatch):
    """The reconcile pass must search ALL stages, unlike the New-only inbound poll."""
    from src.agents import inbound_poller
    from src.integrations.hubspot_models import TicketDTO

    monkeypatch.setattr(inbound_poller, "SessionLocal", db)
    captured: dict = {}

    class FakeHubSpot:
        def search_tickets_sync(self, created_after, pipeline_stage=None, limit=100):
            captured["pipeline_stage"] = pipeline_stage
            return [TicketDTO(id=TICKET, pipeline_stage="1193733925")]

    monkeypatch.setattr(inbound_poller, "HubSpotClient", lambda *a, **k: FakeHubSpot())

    assert inbound_poller.reconcile_ticket_stages_once() == 1
    assert captured["pipeline_stage"] is None, "reconcile must not filter to one stage"

    with db() as session:
        conv = session.query(Conversation).filter_by(hubspot_ticket_id=TICKET).one()
        assert conv.stage == "negotiation"


def test_reconcile_survives_one_bad_ticket(db, stages, monkeypatch):
    """A single failure must not abort the sweep or lose the other updates."""
    from src.agents import inbound_poller
    from src.integrations.hubspot_models import TicketDTO

    monkeypatch.setattr(inbound_poller, "SessionLocal", db)

    class FakeHubSpot:
        def search_tickets_sync(self, created_after, pipeline_stage=None, limit=100):
            return [
                TicketDTO(id="boom", pipeline_stage="1196772135"),
                TicketDTO(id=TICKET, pipeline_stage="1196772135"),
            ]

    monkeypatch.setattr(inbound_poller, "HubSpotClient", lambda *a, **k: FakeHubSpot())

    real = stage_sync.sync_stage_from_hubspot

    def flaky(ticket_id, stage_id, source="hubspot"):
        if ticket_id == "boom":
            raise RuntimeError("HubSpot hiccup")
        return real(ticket_id, stage_id, source=source)

    with patch.object(stage_sync, "sync_stage_from_hubspot", flaky):
        assert inbound_poller.reconcile_ticket_stages_once() == 1

    with db() as session:
        assert session.query(Conversation).filter_by(hubspot_ticket_id=TICKET).one().stage == "won"


# ---- A human answered in HubSpot while our draft was still waiting ----------------


def _draft(db, status: str = "pending_approval", variant: str | None = None) -> int:
    from src.db.models import Message

    with db() as session:
        conv = session.query(Conversation).filter_by(hubspot_ticket_id=TICKET).one()
        msg = Message(
            conversation_id=conv.id,
            direction="outgoing",
            channel="email",
            subject="RE: 문의",
            body="초안",
            status=status,
            prompt_variant=variant,
        )
        session.add(msg)
        session.commit()
        return msg.id


def test_a_draft_is_retired_when_hubspot_moves_the_ticket_on(db, stages):
    """The reason 발송 대기 shows rows whose Stage is not New.

    Drafts are only written for New tickets. Seeing the ticket in a later stage means
    someone already replied in HubSpot — real work carried on while sending was paused —
    so asking the operator to send the draft would answer the customer twice.
    """
    from src.db.models import Message

    draft_id = _draft(db)
    assert stage_sync.sync_stage_from_hubspot(TICKET, STAGE_IDS["HUBSPOT_TICKET_STAGE_NEGOTIATION"])

    with db() as session:
        assert session.get(Message, draft_id).status == "superseded"


def test_a_draft_survives_a_move_that_is_still_new(db, stages):
    from src.db.models import Message

    with db() as session:
        conv = session.query(Conversation).filter_by(hubspot_ticket_id=TICKET).one()
        conv.stage = "negotiation"
        session.commit()

    draft_id = _draft(db)
    assert stage_sync.sync_stage_from_hubspot(TICKET, STAGE_IDS["HUBSPOT_TICKET_STAGE_NEW"]) == "new"

    with db() as session:
        assert session.get(Message, draft_id).status == "pending_approval"


def test_a_draft_still_being_written_is_left_alone(db, stages):
    """`drafting` is mid-flight in the inbound worker, which would write over this."""
    from src.db.models import Message

    draft_id = _draft(db, status="drafting")
    stage_sync.sync_stage_from_hubspot(TICKET, STAGE_IDS["HUBSPOT_TICKET_STAGE_WON"])

    with db() as session:
        assert session.get(Message, draft_id).status == "drafting"


def test_retired_drafts_leave_the_waiting_queue():
    """`superseded` is finished work: out of 발송 대기, visible under 발송 완료."""
    from src.api.routes.messages import LIST_STATUS_BUCKETS

    assert "superseded" not in LIST_STATUS_BUCKETS["awaiting"]
    assert "superseded" in LIST_STATUS_BUCKETS["sent"]


def test_an_approved_draft_is_retired_too(db, stages):
    """발송 워커는 status 만 보고 집어 갑니다 — 승인만 되고 아직 안 나간 회신을 남겨 두면,
    단계가 옮겨진 뒤에도 고객에게 메일이 갑니다."""
    from src.db.models import Message

    draft_id = _draft(db, status="approved")
    stage_sync.sync_stage_from_hubspot(TICKET, STAGE_IDS["HUBSPOT_TICKET_STAGE_AFTER_SEND"])

    with db() as session:
        assert session.get(Message, draft_id).status == "superseded"


def test_a_queued_receipt_acknowledgement_is_never_retired(db, stages):
    """접수확인은 초안이 아닙니다.

    사람이 검토하는 회신이 아니라 "문의 잘 받았습니다" 자동 응답이고, ``approved`` 로 발송
    큐에 앉아 있습니다. 단계 한 번 옮기는 것이 아직 안 나간 고객 접수확인을 취소하면, 고객은
    아무 답도 못 받습니다.
    """
    from src.db.models import Message

    ack_id = _draft(db, status="approved", variant="auto_ack")
    stage_sync.sync_stage_from_hubspot(TICKET, STAGE_IDS["HUBSPOT_TICKET_STAGE_NEGOTIATION"])

    with db() as session:
        assert session.get(Message, ack_id).status == "approved"


def test_a_draft_that_lands_after_the_move_is_retired_without_another_move(db, stages):
    """단계는 이미 옮겨져 있고, 그 뒤에 초안이 생긴 경우.

    초안 작성은 몇 분이 걸립니다. 그 사이에 미팅 링크가 나가면 단계 이동은 이미 지나갔고,
    그 대화에는 다시 아무 이벤트도 오지 않습니다. 단계가 '바뀔 때만' 훑으면 이 초안은
    영영 발송 대기에 남습니다.
    """
    from src.db.models import Message

    with db() as session:
        conv = session.query(Conversation).filter_by(hubspot_ticket_id=TICKET).one()
        conv.stage = "meeting_link_sent"
        session.commit()

    draft_id = _draft(db)
    # 같은 단계를 다시 알려 옵니다 — 이동이 아니므로 반환값은 None 입니다.
    assert (
        stage_sync.sync_stage_from_hubspot(TICKET, STAGE_IDS["HUBSPOT_TICKET_STAGE_AFTER_SEND"])
        is None
    )

    with db() as session:
        assert session.get(Message, draft_id).status == "superseded"


def test_an_unmapped_stage_is_not_past_new(db, stages):
    """모델 기본값 "initial" 은 '단계가 옮겨졌다' 가 아닙니다 — `!= "new"` 로 세면 아직
    아무도 손대지 않은 티켓의 초안까지 종료됩니다."""
    from src.db.models import Message

    draft_id = _draft(db)
    with db() as session:
        conv = session.query(Conversation).filter_by(hubspot_ticket_id=TICKET).one()
        assert stage_sync._retire_superseded_drafts(session, conv.id, "initial") == 0
        session.commit()

    with db() as session:
        assert session.get(Message, draft_id).status == "pending_approval"


def test_a_stage_moved_in_the_console_retires_the_draft(monkeypatch):
    """보드에서 카드를 옮기는 것도 단계 이동입니다. HubSpot 을 거치지 않는 경로라
    stage_sync 가 다시 오지 않고, 10분 폴러의 stage reconcile 은 HubSpot 에서 **최근에
    바뀐** 티켓만 훑습니다."""
    from sqlalchemy.pool import StaticPool

    from src.api.routes import customer_ops
    from src.db.base import Base
    from src.db.models import Message

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(customer_ops, "SessionLocal", factory)

    with factory() as session:
        contact = Contact(normalized_email="board@example.com", full_name="Board")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="new")
        session.add(conv)
        session.flush()
        draft = Message(
            conversation_id=conv.id,
            direction="outgoing",
            channel="email",
            subject="RE: 문의",
            body="초안",
            status="pending_approval",
        )
        session.add(draft)
        session.commit()
        conv_id, draft_id = conv.id, draft.id

    customer_ops._set_conversation_stage(conv_id, "meeting_link_sent")

    with factory() as session:
        assert session.get(Message, draft_id).status == "superseded"
