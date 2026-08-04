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


def _hubspot_says_gone(monkeypatch):
    """Every ticket lookup 404s, the way a deleted one does — and the way an id from
    another portal, or one we recorded wrong, also does."""
    from src.agents import hubspot_reconcile

    class Gone:
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


def test_the_confirmed_pass_retires_and_closes(monkeypatch, waiting_draft):
    """Once the operator has seen the count and said yes."""
    from src.agents.hubspot_reconcile import reconcile_with_hubspot

    _hubspot_says_gone(monkeypatch)
    _contact_id, conversation_id, message_id = waiting_draft

    report = reconcile_with_hubspot(apply=True)

    assert report["retired"] == 1
    with SessionLocal() as session:
        assert session.get(Message, message_id).status == "superseded"
        # Closed, not deleted: the thread still answers "why did this never get a reply".
        assert session.get(Conversation, conversation_id).stage == "closed"


def test_an_auth_failure_is_not_a_deleted_ticket(monkeypatch, waiting_draft):
    """401 means the token is wrong. Reporting that as "this ticket was deleted" would
    invite the operator to confirm away every draft they have."""
    from src.agents import hubspot_reconcile

    class Unauthorized:
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


def test_it_only_looks_at_threads_still_holding_an_answer(monkeypatch, waiting_draft):
    """A thread we already replied to can be out of date without anyone being asked to do
    the wrong thing. Checking every ticket we ever saw would be a lot of API calls to
    learn nothing actionable."""
    from src.agents.hubspot_reconcile import _open_ticket_ids

    _contact_id, conversation_id, message_id = waiting_draft
    assert (conversation_id, "99999999") in _open_ticket_ids()

    with SessionLocal() as session:
        session.get(Message, message_id).status = "sent"
        session.commit()
    assert (conversation_id, "99999999") not in _open_ticket_ids()
