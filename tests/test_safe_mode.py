"""Pre-launch safety guarantees — the operator's hard "대전제".

Pins the two invariants that must hold at ANY time until go-live:
  1. No write ever reaches the real HubSpot account (or the shared Sheet).
  2. No email ever reaches a real customer — every send is forced to ronald@….

If any of these fail, a test/migration run could touch real customer data.
"""

from __future__ import annotations

import asyncio
import smtplib

import pytest

from src.common import safe_mode
from src.common.config import settings
from src.common.safe_mode import (
    ExternalWriteBlocked,
    PRELAUNCH_TEST_RECIPIENT,
    guard_external_write,
    resolve_send_override,
)


@pytest.fixture()
def safe(monkeypatch):
    """Force pre-launch safe mode (external writes disabled)."""
    monkeypatch.setattr(settings, "LIVE_EXTERNAL_WRITES", False)


@pytest.fixture()
def live(monkeypatch):
    """Force live mode (external writes allowed)."""
    monkeypatch.setattr(settings, "LIVE_EXTERNAL_WRITES", True)


# ---- The switch itself -------------------------------------------------------

def test_default_is_safe(monkeypatch):
    """With nothing configured, the code default must be blocked, never live.

    (conftest sets LIVE_EXTERNAL_WRITES=true for the suite; drop it here to assert
    the bare in-code default that ships to production.)"""
    from src.common.config import Settings

    monkeypatch.delenv("LIVE_EXTERNAL_WRITES", raising=False)
    assert Settings(_env_file=None).LIVE_EXTERNAL_WRITES is False


def test_guard_blocks_in_safe_mode(safe):
    assert safe_mode.safe_mode() is True
    with pytest.raises(ExternalWriteBlocked):
        guard_external_write("test:write")


def test_guard_allows_in_live_mode(live):
    assert safe_mode.safe_mode() is False
    guard_external_write("test:write")  # must not raise


# ---- HubSpot writes are hard-blocked ----------------------------------------

def test_hubspot_ticket_stage_blocked(safe):
    from src.integrations.hubspot import HubSpotClient

    with pytest.raises(ExternalWriteBlocked):
        HubSpotClient().update_ticket_stage_sync("123", "stage-x")


def test_hubspot_inbound_status_blocked(safe):
    from src.integrations.hubspot import HubSpotClient

    with pytest.raises(ExternalWriteBlocked):
        HubSpotClient().update_inbound_status_sync("123", "analyzed")


def test_hubspot_update_contact_blocked(safe):
    from src.integrations.hubspot import HubSpotClient

    with pytest.raises(ExternalWriteBlocked):
        asyncio.run(HubSpotClient().update_contact("123", {"x": "y"}))


def test_hubspot_timeline_email_blocked(safe):
    from src.integrations.hubspot import HubSpotClient

    with pytest.raises(ExternalWriteBlocked):
        asyncio.run(HubSpotClient().create_email_engagement("123", "subj", "body"))


def test_move_ticket_stage_after_send_never_writes(safe):
    """The post-send helper swallows the block and reports failure, never writing."""
    from src.integrations.hubspot import move_ticket_stage_after_send

    monkey_target = settings.HUBSPOT_TICKET_STAGE_AFTER_SEND
    settings.HUBSPOT_TICKET_STAGE_AFTER_SEND = "stage-after"
    try:
        assert move_ticket_stage_after_send("123") is False
    finally:
        settings.HUBSPOT_TICKET_STAGE_AFTER_SEND = monkey_target


# ---- Google Sheets writes are disabled --------------------------------------

def test_sheets_writes_disabled_in_safe_mode(safe, monkeypatch):
    from src.integrations import google_sheets

    monkeypatch.setattr(google_sheets, "is_configured", lambda: True)
    assert google_sheets.writes_enabled() is False


def test_sheets_writes_enabled_when_live(live, monkeypatch):
    from src.integrations import google_sheets

    monkeypatch.setattr(google_sheets, "is_configured", lambda: True)
    assert google_sheets.writes_enabled() is True


# ---- Every email is forced to the test recipient ----------------------------

def test_override_forces_ronald_in_safe_mode(safe, monkeypatch):
    monkeypatch.setattr(settings, "SEND_OVERRIDE_EMAIL", "")
    assert resolve_send_override() == PRELAUNCH_TEST_RECIPIENT == "ronald@estsoft.com"


def test_explicit_override_still_wins_in_safe_mode(safe, monkeypatch):
    monkeypatch.setattr(settings, "SEND_OVERRIDE_EMAIL", "qa@example.com")
    assert resolve_send_override() == "qa@example.com"


def test_no_override_when_live(live, monkeypatch):
    monkeypatch.setattr(settings, "SEND_OVERRIDE_EMAIL", "")
    assert resolve_send_override() == ""


def test_send_smtp_forces_recipient_to_ronald(safe, monkeypatch):
    """Even a direct send_smtp() call to a customer address is rerouted to ronald."""
    from src.db.models import Message
    from src.integrations.senders import smtp

    monkeypatch.setattr(settings, "SEND_OVERRIDE_EMAIL", "")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "user")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "pass")
    monkeypatch.setattr(settings, "SMTP_FROM_EMAIL", "sales@estsoft.com")
    monkeypatch.setattr(settings, "SMTP_FROM_NAME", "Sales")

    captured: dict = {}

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def starttls(self, *a, **k):
            pass

        def login(self, *a, **k):
            pass

        def send_message(self, msg):
            captured["msg"] = msg

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    msg = Message(
        to_address="real.customer@bigcorp.com",
        subject="Hello",
        body="hi",
        language="en",
        signature_key=None,
    )
    smtp.send_smtp(msg)

    assert "ronald@estsoft.com" in captured["msg"]["To"]
    assert "bigcorp.com" not in captured["msg"]["To"]
    # The caller's ORM row must not be mutated by the reroute.
    assert msg.to_address == "real.customer@bigcorp.com"


# ---- The temporary hard no-send switch ---------------------------------------
# safe_mode.EMAIL_SENDING_ENABLED is False in shipped code. The autouse conftest
# fixture lifts it for the suite, so these tests put it back to its real value.


@pytest.fixture()
def no_send(monkeypatch):
    """Restore the shipped value of the operator's no-send switch."""
    monkeypatch.setattr(safe_mode, "EMAIL_SENDING_ENABLED", False)


def test_no_send_switch_ships_disabled():
    """The constant must stay False until the operator deliberately re-enables it."""
    import ast
    import pathlib

    source = pathlib.Path(safe_mode.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    value = next(
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "EMAIL_SENDING_ENABLED" for t in node.targets)
    )
    assert value is False, "EMAIL_SENDING_ENABLED was re-enabled in source"


def test_no_send_switch_blocks_smtp_entirely(no_send, monkeypatch):
    """Not even the pre-launch test recipient is emailed while the switch is off."""
    from src.db.models import Message
    from src.integrations.senders import smtp

    # Fully configured SMTP + live mode: only the switch stands in the way.
    monkeypatch.setattr(settings, "LIVE_EXTERNAL_WRITES", True)
    monkeypatch.setattr(settings, "SEND_OVERRIDE_EMAIL", "")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "user")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "pass")
    monkeypatch.setattr(settings, "SMTP_FROM_EMAIL", "sales@estsoft.com")

    def _explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("smtplib.SMTP was constructed despite the no-send switch")

    monkeypatch.setattr(smtplib, "SMTP", _explode)

    msg = Message(
        to_address="real.customer@bigcorp.com",
        subject="Hello",
        body="hi",
        language="en",
        signature_key=None,
    )
    with pytest.raises(smtp.SMTPSendingDisabled):
        smtp.send_smtp(msg)


def test_no_send_switch_marks_message_failed_not_sent(no_send, monkeypatch):
    """A blocked send must never leave the row looking delivered."""
    import asyncio

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.agents import send_worker
    from src.db.base import Base
    from src.db.models import Contact, Conversation, Message

    monkeypatch.setattr(settings, "SMTP_USERNAME", "user")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "pass")
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: pytest.fail("SMTP opened"))

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(send_worker, "SessionLocal", factory)
    session = factory()

    contact = Contact(normalized_email="buyer@bigcorp.com", full_name="Buyer")
    session.add(contact)
    session.flush()
    conv = Conversation(contact_id=contact.id)
    session.add(conv)
    session.flush()
    msg = Message(
        conversation_id=conv.id,
        direction="outgoing",
        body="Hi",
        status="approved",
        to_address="buyer@bigcorp.com",
    )
    session.add(msg)
    session.commit()
    mid = msg.id

    send_worker._claim_ready_id()
    assert asyncio.run(send_worker._send_one(mid)) is False

    session.expire_all()
    stored = session.get(Message, mid)
    assert stored.status == "send_failed"
    assert stored.sent_at is None
    session.close()


# ---- Safe mode must not blind our own local bookkeeping ----------------------
# Safe mode blocks EXTERNAL side effects. It must still record what *we* did, or
# every elapsed-time view ("no reply for N days", reply rate, pipeline stage) is
# silently empty until go-live and the whole flow becomes untestable pre-launch.


@pytest.mark.asyncio
async def test_safe_mode_still_records_local_send_bookkeeping(safe, monkeypatch):
    """test_sent must still stamp last_outgoing_at/stage — but skip HubSpot & Sheets."""
    from unittest.mock import AsyncMock

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.agents import send_worker
    from src.db.base import Base
    from src.db.models import Contact, Conversation, Message

    monkeypatch.setattr(settings, "SEND_OVERRIDE_EMAIL", "")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(send_worker, "SessionLocal", factory)
    session = factory()

    contact = Contact(normalized_email="buyer@bigcorp.com", full_name="Buyer")
    session.add(contact)
    session.flush()
    conv = Conversation(contact_id=contact.id)
    session.add(conv)
    session.flush()
    msg = Message(conversation_id=conv.id, direction="outgoing", body="Hi", status="approved")
    session.add(msg)
    session.commit()
    mid = msg.id

    send_worker._claim_ready_id()
    monkeypatch.setattr("src.integrations.senders.send", AsyncMock())
    bookkeeping = AsyncMock()
    monkeypatch.setattr(send_worker, "_post_send_bookkeeping", bookkeeping)

    assert await send_worker._send_one(mid) is True

    session.expire_all()
    stored = session.get(Message, mid)
    assert stored.status == "test_sent"          # safe mode marker
    assert stored.conversation.last_outgoing_at is not None   # the regression
    assert stored.conversation.stage == "meeting_link_sent"   # the regression
    bookkeeping.assert_not_awaited()             # external writes still blocked
    session.close()


# The matching guarantee for inbound — a customer answering a safe-mode ("test_sent")
# reply must still set Message.replied — is pinned by
# tests/test_inbound_flow.py::test_test_sent_reply_is_marked_answered_in_safe_mode,
# which already has the LLM/knowledge fixtures that path needs.
