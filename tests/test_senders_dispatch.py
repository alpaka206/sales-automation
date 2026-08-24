"""Tests for the inbound reply sender dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.senders import send


def _make_message(**overrides) -> MagicMock:
    msg = MagicMock()
    msg.id = overrides.get("id", 1)
    msg.channel = overrides.get("channel", "email")
    msg.direction = overrides.get("direction", "outgoing")
    msg.to_address = overrides.get("to_address", "to@example.com")
    msg.subject = overrides.get("subject", "Test")
    msg.body = overrides.get("body", "Hello")
    msg.language = overrides.get("language", "ko")
    msg.target_language = overrides.get("target_language", None)
    msg.prompt_variant = overrides.get("prompt_variant", None)
    msg.signature_key = overrides.get("signature_key", "none")
    msg.conversation = MagicMock()
    msg.conversation.contact_id = overrides.get("contact_id", 100)
    msg.conversation.contact = None
    return msg


@pytest.mark.asyncio
@patch("src.integrations.senders._log_hubspot_email", new_callable=AsyncMock)
@patch("src.integrations.senders.send_smtp")
async def test_smtp_sends_then_logs_to_hubspot(mock_smtp, mock_log) -> None:
    msg = _make_message()
    await send(msg)
    mock_smtp.assert_called_once_with(msg)
    mock_log.assert_awaited_once_with(msg, msg)


@pytest.mark.asyncio
@patch("src.integrations.senders._log_hubspot_email", new_callable=AsyncMock)
@patch("src.integrations.senders.send_smtp")
async def test_a_rerouted_send_is_still_written_to_the_history(
    mock_smtp, mock_log, monkeypatch
) -> None:
    """A test send is a gap in the customer's history if it is not logged.

    When FORCE_TEST_RECIPIENT is on, every send is rerouted, so skipping the log for
    rerouted mail would leave the HubSpot timeline empty. What gets logged
    is the copy that actually went out — subject marker included, so it cannot be mistaken
    for a real reply — while the engagement id is stamped on the original row.
    """
    from src.common import safe_mode

    monkeypatch.setattr(safe_mode, "FORCE_TEST_RECIPIENT", True)
    monkeypatch.setattr(safe_mode.settings, "SEND_OVERRIDE_EMAIL", "ronald@estsoft.com")

    row = _make_message(to_address="real.customer@bigcorp.com", subject="답변드립니다")
    await send(row)

    mock_log.assert_awaited_once()
    sent, stamped = mock_log.await_args.args
    assert stamped is row                                  # id lands on the ORM record
    assert sent is not row                                 # what left was the copy
    assert sent.to_address == "ronald@estsoft.com"
    assert sent.subject.startswith("[TEST→real.customer@bigcorp.com]")
    assert row.to_address == "real.customer@bigcorp.com"   # the row is never mutated
