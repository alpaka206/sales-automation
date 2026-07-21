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
