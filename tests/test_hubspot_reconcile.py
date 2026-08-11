"""The 최신화 button on 운영 로그.

HubSpot and our copy drift: a ticket answered or deleted there while our queue still
shows an unsent draft, so the operator is asked to send a reply the customer already has
— or one for a thread that no longer exists.

The property worth pinning is not that it syncs. It is that a 404 alone never retires
anyone's draft.
"""

from __future__ import annotations

import httpx
import pytest

from src.db.models import Contact, Conversation, Message
from src.db.session import SessionLocal


@pytest.fixture
def waiting_draft():
    """One thread holding an unsent draft against a ticket id."""
    with SessionLocal() as session:
        contact = Contact(email="drift@example.com", normalized_email="drift@example.com",
                          full_name="드리프트", company="드리프트 주식회사")
        session.add(contact)
        session.flush()
        contact_id = contact.id
        conversation = Conversation(
            contact_id=contact_id, stage="new", hubspot_ticket_id="99999999"
        )
        session.add(conversation)
        session.flush()
        conversation_id = conversation.id
        message = Message(
            conversation_id=conversation_id, direction="outgoing",
            status="pending_approval", subject="RE: 문의", body="초안",
        )
        session.add(message)
        session.flush()
        message_id = message.id
        session.commit()
        ids = (contact_id, conversation_id, message_id)

    yield ids

    # Child rows first, by hand: the ORM nullifies the FK on a bare parent delete and
    # every one of these columns is NOT NULL. The confirmed pass also writes a progress
    # row, so that one has to go too.
    from sqlalchemy import delete as sql_delete

    from src.db.models import ConversationProgress

    contact_id, conversation_id, _message_id = ids
    with SessionLocal() as session:
        session.execute(
            sql_delete(ConversationProgress).where(
                ConversationProgress.conversation_id == conversation_id
            )
        )
        session.execute(sql_delete(Message).where(Message.conversation_id == conversation_id))
        session.execute(sql_delete(Conversation).where(Conversation.id == conversation_id))
        session.execute(sql_delete(Contact).where(Contact.id == contact_id))
        session.commit()


GONE_TICKET = "99999999"


def _hubspot_says_gone(monkeypatch):
    """This one ticket is missing, the way a deleted one is — and the way an id from
    another portal, or one we recorded wrong, also is.

    Only this one: both the batch check and the per-ticket lookup answer for whatever
    else the shared test database happens to hold, and "everything is gone" would have
    the confirmed pass delete the other tests' rows along with ours.
    """
    from src.agents import hubspot_reconcile

    class Gone:
        def existing_ticket_ids_sync(self, ticket_ids):
            return {str(t) for t in ticket_ids} - {GONE_TICKET}

        def get_ticket_sync(self, ticket_id):
            request = httpx.Request("GET", f"https://api.hubspot.com/tickets/{ticket_id}")
            raise httpx.HTTPStatusError(
                "not found", request=request, response=httpx.Response(404, request=request)
            )

    monkeypatch.setattr(hubspot_reconcile, "HubSpotClient", lambda: Gone())
    monkeypatch.setattr(
        "src.agents.inbound_poller.reconcile_ticket_stages_once", lambda: 0
    )


def test_a_404_alone_never_retires_a_draft(monkeypatch, waiting_draft):
    """404 is how a DELETED ticket looks. It is also how a ticket id belonging to another
    portal looks, and a backfilled row, and an id we stored wrong. Acting on it
    unprompted is how a button meant to tidy the queue throws away an answer nobody had
    sent yet — which is exactly what the first version of this did on its first run.
    """
    from src.agents.hubspot_reconcile import reconcile_with_hubspot

    _hubspot_says_gone(monkeypatch)
    _contact_id, _conversation_id, message_id = waiting_draft

    report = reconcile_with_hubspot()

    assert report["deleted"] == 1, "it still has to SAY the ticket is missing"
    assert report["retired"] == 0, "but it must not act on that alone"
    assert report["applied"] is False
    with SessionLocal() as session:
        assert session.get(Message, message_id).status == "pending_approval"


def test_the_confirmed_pass_deletes_the_thread(monkeypatch, waiting_draft):
    """Once the operator has seen the count and said yes. The ticket was deleted in
    HubSpot, so the thread goes with it."""
    from src.agents.hubspot_reconcile import reconcile_with_hubspot

    _hubspot_says_gone(monkeypatch)
    _contact_id, conversation_id, message_id = waiting_draft

    report = reconcile_with_hubspot(apply=True)

    assert report["retired"] == 1
    with SessionLocal() as session:
        assert session.get(Message, message_id) is None
        assert session.get(Conversation, conversation_id) is None


def test_deleting_a_thread_never_takes_the_customer_or_the_money(waiting_draft):
    """The blast radius, asserted rather than assumed. A ticket deleted in HubSpot says
    nothing about whether the customer is real or whether they signed something."""
    from decimal import Decimal

    from src.agents.hubspot_reconcile import delete_conversation
    from src.db.models import ContractRecord, CustomerInteraction

    contact_id, conversation_id, _message_id = waiting_draft
    with SessionLocal() as session:
        session.add(ContractRecord(contact_id=contact_id, conversation_id=conversation_id,
                                   status="active", amount=Decimal("1000"), currency="USD"))
        session.add(CustomerInteraction(contact_id=contact_id, conversation_id=conversation_id,
                                        channel="meeting", summary="미팅 요약"))
        session.commit()

    delete_conversation(conversation_id, "99999999")

    with SessionLocal() as session:
        assert session.get(Conversation, conversation_id) is None
        assert session.get(Contact, contact_id) is not None, "the person is still real"
        contract = session.query(ContractRecord).filter_by(contact_id=contact_id).one()
        assert contract.amount == Decimal("1000"), "a contract is never deleted for this"
        assert contract.conversation_id is None, "just detached"
        note = session.query(CustomerInteraction).filter_by(contact_id=contact_id).one()
        assert note.summary == "미팅 요약", "the meeting still happened"


def test_a_deletion_webhook_removes_the_thread(monkeypatch, waiting_draft):
    """HubSpot tells us when a ticket is deleted and nothing used to listen, so the draft
    sat in 발송 대기 waiting on a thread that had stopped existing."""
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.common.config import settings

    _contact_id, conversation_id, _message_id = waiting_draft
    with (
        patch.object(settings, "HUBSPOT_WEBHOOK_SECRET", ""),
        patch.object(settings, "HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE", False),
        TestClient(app) as client,
    ):
        response = client.post(
            "/webhooks/hubspot",
            json=[{"subscriptionType": "ticket.deletion", "objectId": 99999999,
                   "eventId": 1, "occurredAt": 1}],
            headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
        )
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["status"] == "deleted"
    with SessionLocal() as session:
        assert session.get(Conversation, conversation_id) is None


def test_a_deleted_ticket_is_never_queued_as_inbound_work():
    """Fetching a ticket that no longer exists is not work. Mapping the subscription type
    would have enqueued exactly that."""
    from src.api import webhook

    assert "ticket.deletion" not in webhook._HUBSPOT_SUBSCRIPTION_MAP


def test_an_auth_failure_is_not_a_deleted_ticket(monkeypatch, waiting_draft):
    """401 means the token is wrong. Reporting that as "this ticket was deleted" would
    invite the operator to confirm away every draft they have."""
    from src.agents import hubspot_reconcile

    class Unauthorized:
        def existing_ticket_ids_sync(self, ticket_ids):
            raise RuntimeError("tickets batch read failed (401)")

        def get_ticket_sync(self, ticket_id):
            request = httpx.Request("GET", "https://api.hubspot.com/tickets/1")
            raise httpx.HTTPStatusError(
                "unauthorized", request=request,
                response=httpx.Response(401, request=request),
            )

    monkeypatch.setattr(hubspot_reconcile, "HubSpotClient", lambda: Unauthorized())
    monkeypatch.setattr("src.agents.inbound_poller.reconcile_ticket_stages_once", lambda: 0)

    report = hubspot_reconcile.reconcile_with_hubspot(apply=True)

    assert report["deleted"] == 0
    assert report["retired"] == 0
    # 그리고 **왜** 아무것도 안 나왔는지 화면에 적힙니다. 조용히 건너뛰면 「정리할 항목
    # 없음」이 정말 없는 것과 보지 못한 것 두 가지를 뜻하고, 운영자에게는 똑같이 보입니다.
    assert report["error"] and "401" in report["error"]


def test_only_threads_holding_an_answer_are_asked_about_their_stage(monkeypatch, waiting_draft):
    """A thread we already replied to can be out of date about its STAGE without anyone
    being asked to do the wrong thing, and the poller sweeps those anyway. Existence is
    the other half and is checked for everything — see the test below."""
    from src.agents.hubspot_reconcile import _all_ticket_ids, _open_ticket_ids

    _contact_id, conversation_id, message_id = waiting_draft
    assert (conversation_id, GONE_TICKET) in _open_ticket_ids()

    with SessionLocal() as session:
        session.get(Message, message_id).status = "sent"
        session.commit()
    assert (conversation_id, GONE_TICKET) not in _open_ticket_ids()
    assert (conversation_id, GONE_TICKET) in _all_ticket_ids()


def test_a_deleted_ticket_leaves_the_board_with_no_draft_to_find_it_by(
    monkeypatch, waiting_draft
):
    """The one the operator hit: a ticket deleted in HubSpot kept its card on the
    파이프라인 board.

    Three things look for absence and none of them covered this. The deletion webhook
    fires once — if it was never subscribed to, or never arrived, it never comes again.
    The 10-minute poller sweeps the tickets HubSpot HAS, so a ticket it no longer has
    appears in no sweep. And 최신화 asked only about threads holding an unsent draft,
    which an answered 협상중 or Won card has none of.
    """
    from src.agents import hubspot_reconcile

    _contact_id, conversation_id, message_id = waiting_draft
    with SessionLocal() as session:
        session.get(Message, message_id).status = "sent"
        session.commit()

    _hubspot_says_gone(monkeypatch)
    report = hubspot_reconcile.reconcile_with_hubspot(apply=True)

    assert report["deleted"] == 1
    with SessionLocal() as session:
        assert session.get(Conversation, conversation_id) is None


def test_a_thread_past_new_has_its_draft_retired_not_deleted(monkeypatch, waiting_draft):
    """After 최신화 only New keeps a waiting draft. Anything past it was answered in
    HubSpot, so our draft is a reply the customer already has.

    Retired, not deleted, and the difference is the point: the ticket still EXISTS. Only
    a ticket that is gone from HubSpot earns a delete.
    """
    from src.agents import hubspot_reconcile

    class Negotiating:
        def existing_ticket_ids_sync(self, ticket_ids):
            return {str(t) for t in ticket_ids}

        def get_ticket_sync(self, ticket_id):
            class Ticket:
                id = ticket_id
                pipeline_stage = "negotiating-stage-id"
            return Ticket()

    monkeypatch.setattr(hubspot_reconcile, "HubSpotClient", lambda: Negotiating())
    monkeypatch.setattr("src.agents.inbound_poller.reconcile_ticket_stages_once", lambda: 0)
    monkeypatch.setattr("src.agents.stage_sync.local_stage_for", lambda _id: "negotiation")
    monkeypatch.setattr("src.agents.stage_sync.sync_stage_from_hubspot",
                        lambda *a, **k: None)

    _contact_id, conversation_id, message_id = waiting_draft
    dry = hubspot_reconcile.reconcile_with_hubspot()
    assert dry["stale"] == 1 and dry["retired"] == 0

    hubspot_reconcile.reconcile_with_hubspot(apply=True)
    with SessionLocal() as session:
        assert session.get(Message, message_id).status == "superseded"
        assert session.get(Conversation, conversation_id) is not None, "the ticket exists"
