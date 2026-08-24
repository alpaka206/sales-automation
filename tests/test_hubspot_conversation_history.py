"""Keep our Conversations replies distinct from human replies during history sync."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.api.routes.customer_ops import _retire_drafts_for_replies_seen_in_hubspot
from src.db.models import Contact, Conversation, Message
from src.integrations.hubspot_models import EngagementDTO


def test_our_conversation_reply_does_not_retire_a_pending_draft(db_session) -> None:
    now = datetime.now(timezone.utc)
    contact = Contact(normalized_email="history@example.com", full_name="History")
    db_session.add(contact)
    db_session.flush()
    conversation = Conversation(
        contact_id=contact.id,
        hubspot_ticket_id="ticket-1",
        stage="new",
    )
    db_session.add(conversation)
    db_session.flush()
    sent = Message(
        conversation_id=conversation.id,
        direction="outgoing",
        status="sent",
        subject="Re: Inquiry",
        body="sent",
        sent_at=now,
        hubspot_thread_id="thread-1",
        hubspot_message_id="conversation-message-1",
    )
    draft = Message(
        conversation_id=conversation.id,
        direction="outgoing",
        status="pending_approval",
        subject="Follow-up",
        body="draft",
        created_at=now - timedelta(minutes=10),
    )
    db_session.add_all([sent, draft])
    db_session.flush()

    _retire_drafts_for_replies_seen_in_hubspot(
        db_session,
        contact.id,
        [
            EngagementDTO(
                id="crm-email-1",
                type="outgoing_email",
                subject="Re: Inquiry",
                timestamp=now + timedelta(seconds=20),
                ticket_id="ticket-1",
            )
        ],
    )

    assert draft.status == "pending_approval"


def test_a_separate_human_reply_retires_the_pending_draft(db_session) -> None:
    now = datetime.now(timezone.utc)
    contact = Contact(normalized_email="human-reply@example.com", full_name="Human")
    db_session.add(contact)
    db_session.flush()
    conversation = Conversation(
        contact_id=contact.id,
        hubspot_ticket_id="ticket-2",
        stage="new",
    )
    db_session.add(conversation)
    db_session.flush()
    draft = Message(
        conversation_id=conversation.id,
        direction="outgoing",
        status="pending_approval",
        subject="Draft",
        body="draft",
        created_at=now - timedelta(minutes=10),
    )
    db_session.add(draft)
    db_session.flush()
    draft_id = draft.id

    _retire_drafts_for_replies_seen_in_hubspot(
        db_session,
        contact.id,
        [
            EngagementDTO(
                id="crm-email-human",
                type="outgoing_email",
                subject="Manual reply",
                timestamp=now,
                ticket_id="ticket-2",
            )
        ],
    )

    assert db_session.get(Message, draft_id) is None
