"""Pre-launch safety guarantees — the operator's hard "대전제".

Pins the two invariants that must hold at ANY time until go-live:
  1. No write ever reaches the real HubSpot account (or the shared Sheet).
  2. No email leaves the service; recipients are never silently rewritten.

If any of these fail, a test/migration run could touch real customer data.
"""

from __future__ import annotations

import asyncio

import pytest

from src.common import safe_mode
from src.common.config import settings
from src.common.safe_mode import (
    ExternalWriteBlocked,
    email_delivery_enabled,
    guard_external_write,
)
from src.integrations.delivery import SendingDisabled


@pytest.fixture()
def safe(monkeypatch):
    """Force pre-launch safe mode (external writes disabled)."""
    monkeypatch.setattr(settings, "LIVE_EXTERNAL_WRITES", False)


@pytest.fixture()
def live(monkeypatch):
    """Force live mode (external writes allowed)."""
    monkeypatch.setattr(settings, "LIVE_EXTERNAL_WRITES", True)
    monkeypatch.setattr(settings, "LIVE_HUBSPOT_WRITES", True)
    monkeypatch.setattr(settings, "LIVE_SHEETS_WRITES", True)


# ---- The suite itself must never reach a real external system ----------------
#
# These assert on conftest's environment, not on a fixture, because that environment IS
# the guarantee. It failed once: the suite runs with LIVE_EXTERNAL_WRITES=true, and
# Sheets was safe only by accident — the grant lived in the database, and the temp
# SQLite had no row. When load_grant() gained an env fallback the accident stopped
# holding, and `pytest` appended 34 fixture rows to the shared sales workbook.

def test_pytest_can_never_write_to_a_real_google_sheet():
    from src.integrations import google_sheets

    assert settings.GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN == ""
    assert settings.GOOGLE_SHEETS_SPREADSHEET_ID == ""
    assert google_sheets.is_configured() is False
    assert google_sheets.writes_enabled() is False


def test_pytest_can_never_move_a_real_hubspot_ticket():
    """No stage id configured means _stage_id() returns "" and no request is sent."""
    from src.agents.stage_sync import LOCAL_STAGE_TO_SETTING

    for attr in LOCAL_STAGE_TO_SETTING.values():
        assert getattr(settings, attr) == "", attr
    assert settings.HUBSPOT_PRIVATE_APP_TOKEN == "test-hubspot-token"


def test_no_setting_can_make_a_detailed_reply_send_itself():
    """Only the receipt acknowledgement is ever unattended.

    That used to depend on AUTO_SEND_THRESHOLD staying above 1.0 — a number in a
    dashboard nobody audits. The branch it guarded is gone, so the guarantee is now
    structural: there is no config key left that could re-open it.
    """
    from src.common.config import Settings

    assert not hasattr(Settings(_env_file=None), "AUTO_SEND_THRESHOLD")


# ---- Per-destination switches are SUBORDINATE to the master ------------------

def test_per_channel_switches_cannot_override_safe_mode(safe, monkeypatch):
    """The whole point of the master: turning a channel on must not open a hole.

    If either of these could let a write through, "is it safe?" would stop being a
    single question and the 대전제 would depend on three variables agreeing.
    """
    from src.integrations import google_sheets

    monkeypatch.setattr(settings, "LIVE_HUBSPOT_WRITES", True)
    monkeypatch.setattr(settings, "LIVE_SHEETS_WRITES", True)
    monkeypatch.setattr(google_sheets, "is_configured", lambda: True)

    assert safe_mode.live_hubspot_writes() is False
    assert safe_mode.live_sheets_writes() is False
    assert google_sheets.writes_enabled() is False
    with pytest.raises(ExternalWriteBlocked):
        guard_external_write("hubspot:update_ticket_stage")


def test_hubspot_can_be_held_back_while_sheets_go_live(live, monkeypatch):
    from src.integrations import google_sheets

    monkeypatch.setattr(settings, "LIVE_HUBSPOT_WRITES", False)
    monkeypatch.setattr(google_sheets, "is_configured", lambda: True)

    with pytest.raises(ExternalWriteBlocked, match="LIVE_HUBSPOT_WRITES"):
        guard_external_write("hubspot:update_ticket_stage")
    assert google_sheets.writes_enabled() is True


def test_sheets_can_be_held_back_while_hubspot_goes_live(live, monkeypatch):
    from src.integrations import google_sheets

    monkeypatch.setattr(settings, "LIVE_SHEETS_WRITES", False)
    monkeypatch.setattr(google_sheets, "is_configured", lambda: True)

    assert google_sheets.writes_enabled() is False
    guard_external_write("hubspot:update_ticket_stage")  # must not raise


def test_an_unregistered_channel_falls_back_to_the_master(safe):
    """A new write path that forgets to register a gate must still be blocked."""
    with pytest.raises(ExternalWriteBlocked):
        guard_external_write("some_new_crm:push")


def test_per_channel_defaults_are_permissive_so_the_master_alone_goes_live(monkeypatch):
    """Flipping only LIVE_EXTERNAL_WRITES must behave exactly as it did before."""
    from src.common.config import Settings

    monkeypatch.delenv("LIVE_HUBSPOT_WRITES", raising=False)
    monkeypatch.delenv("LIVE_SHEETS_WRITES", raising=False)
    bare = Settings(_env_file=None)
    assert bare.LIVE_HUBSPOT_WRITES is True
    assert bare.LIVE_SHEETS_WRITES is True


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


def test_hubspot_record_write_blocked(safe):
    """티켓 세부 내역의 「플랜 정보」 저장. 이 화면에서 유일하게 허브스팟에 쓰는 폼입니다.

    막는 자리가 라우트가 아니라 `update_record_fields` 안이라서, 다음 호출자가 생겨도 그
    앞을 지납니다. 안전 모드에서는 네트워크에 닿기 전에 끝나므로 토큰도 필요 없습니다.
    """
    from src.integrations.hubspot_record import update_record_fields

    with pytest.raises(ExternalWriteBlocked):
        update_record_fields("123", {"user_seq": "184920"})


def test_hubspot_contact_company_write_blocked(safe):
    """연락처 정보의 회사 이름도 허브스팟에 씁니다 (2026-08-19). 새 외부 쓰기 경로이므로
    여기 한 줄이 같이 늘어납니다 — 그것이 이 파일의 규칙입니다."""
    from src.integrations.hubspot import HubSpotClient

    with pytest.raises(ExternalWriteBlocked):
        HubSpotClient().update_contact_company_sync("123", "롯데지알에스")


def test_hubspot_conversation_send_blocked(safe):
    from src.integrations.hubspot import ConversationReplyContext, HubSpotClient

    with pytest.raises(ExternalWriteBlocked):
        asyncio.run(
            HubSpotClient().send_conversation_message(
                ConversationReplyContext("thread", "1002", "account"),
                recipient_email="buyer@example.com",
                subject="subj",
                text="body",
                rich_text="<p>body</p>",
            )
        )


def test_hubspot_interaction_note_blocked(safe):
    """소통 히스토리의 HubSpot 사본. 기록 자체는 우리 DB 에 남고, 이 통로만 막힙니다."""
    from src.integrations.hubspot import HubSpotClient

    with pytest.raises(ExternalWriteBlocked):
        asyncio.run(HubSpotClient().create_interaction_note("123", "전화로 단가 재확인"))


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


def test_won_customer_mirror_is_blocked_in_safe_mode(safe, monkeypatch):
    """수주 고객 탭 동기화도 같은 문 뒤에 있습니다.

    이 경로는 콘솔의 아무 저장에나 붙어 있어서(main.publish_changes_middleware) 안전 모드
    에서 조용히 통과하면 테스트 고객이 공용 워크북에 그대로 쌓입니다. 서비스는 만들기 전에
    막혀야 합니다 — is_configured() 는 참인 채로 검사합니다.
    """
    from src.agents import won_sheets
    from src.integrations import google_sheets

    monkeypatch.setattr(google_sheets, "is_configured", lambda: True)
    monkeypatch.setattr(
        google_sheets, "_build_service", lambda: pytest.fail("safe mode built a Sheets client")
    )
    with pytest.raises(ExternalWriteBlocked):
        won_sheets.sync_won_sheets()


def test_env_refresh_token_does_not_bypass_safe_mode(safe, monkeypatch):
    """A .env-supplied Google account connects the workbook but must not unblock it.

    GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN makes is_configured() true with no operator
    action at all — no click, no database row. That is the whole point of it, and it
    is exactly why it must still land behind LIVE_EXTERNAL_WRITES: otherwise setting
    one env var would silently start writing into the shared sales workbook.
    """
    from src.integrations import google_oauth, google_sheets

    monkeypatch.setattr(settings, "GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN", "1//fake-refresh")
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_ACCOUNT_EMAIL", "owner@estsoft.com")
    # conftest blanks the workbook id for the whole suite; is_configured() needs one.
    monkeypatch.setattr(settings, "GOOGLE_SHEETS_SPREADSHEET_ID", "SHEET123")

    assert google_oauth.env_grant() is not None
    assert google_oauth.load_grant()[0]["refresh_token"] == "1//fake-refresh"
    assert google_sheets.is_configured() is True
    assert google_sheets.writes_enabled() is False
    assert google_sheets.update_inbound_stage(1336, "won") is False


# ---- Email delivery fails closed without changing the recipient -------------

def test_email_delivery_is_blocked_in_safe_mode(safe):
    assert email_delivery_enabled() is False


def test_email_delivery_is_enabled_in_live_mode(live):
    assert email_delivery_enabled() is True


def test_direct_send_is_blocked_in_safe_mode(safe):
    """A caller is blocked before a HubSpot delivery client is opened."""
    from src.db.models import Message
    from src.integrations.senders import send

    msg = Message(
        to_address="real.customer@bigcorp.com",
        subject="Hello",
        body="hi",
        language="en",
        signature_key=None,
    )
    with pytest.raises(SendingDisabled):
        asyncio.run(send(msg))
    assert msg.to_address == "real.customer@bigcorp.com"


# ---- The email constant, as SHIPPED -----------------------------------------
# Sending is on for the real recipient. The value is read out of the source
# file rather than imported, so a monkeypatch elsewhere in the suite cannot make these
# pass: what is asserted here is what the repository actually ships.


@pytest.fixture()
def no_send(monkeypatch):
    """Engage the operator's no-send switch (nothing is emailed at all)."""
    monkeypatch.setattr(safe_mode, "EMAIL_SENDING_ENABLED", False)


def _shipped_constant(name: str):
    import ast
    import pathlib

    source = pathlib.Path(safe_mode.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    return next(
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == name for t in node.targets)
    )


def test_email_ships_live_without_a_forced_test_recipient():
    """Human-approved drafts ship to their real recipient in the deployed posture."""
    assert _shipped_constant("EMAIL_SENDING_ENABLED") is True


def test_no_send_switch_blocks_hubspot_delivery_entirely(no_send, monkeypatch):
    """Not even the pre-launch test recipient is emailed while the switch is off."""
    from src.db.models import Message
    from src.integrations.senders import send

    # Fully live mode: only the code-level switch stands in the way.
    monkeypatch.setattr(settings, "LIVE_EXTERNAL_WRITES", True)

    msg = Message(
        to_address="real.customer@bigcorp.com",
        subject="Hello",
        body="hi",
        language="en",
        signature_key=None,
    )
    with pytest.raises(SendingDisabled):
        asyncio.run(send(msg))


def test_no_send_switch_does_everything_except_the_mail(no_send, monkeypatch):
    """메일만 막고, 나머지는 전부 그대로 — 운영자의 요구입니다.

    검토 완료·발송을 누르면 메일은 나가지 않지만 단계는 옮겨지고 HubSpot·워크북 동기화도
    돕니다. no-send 스위치는 `send_failed`가 아니라 테스트 완료 상태로 처리됩니다.
    누른 사람 눈에는 아무 일도 안 일어난 것으로 보였습니다.

    상태는 `sent` 가 아니라 `test_sent` 입니다: 고객에게 정말 간 것만 `sent` 여야 합니다.
    """
    import asyncio

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.agents import send_worker
    from src.db.base import Base
    from src.db.models import Contact, Conversation, Message

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

    from unittest.mock import AsyncMock

    bookkeeping = AsyncMock()
    monkeypatch.setattr(send_worker, "_post_send_bookkeeping", bookkeeping)

    send_worker._claim_ready_id()
    assert asyncio.run(send_worker._send_one(mid)) is True

    session.expire_all()
    stored = session.get(Message, mid)
    assert stored.status == "test_sent"                       # 나간 적 없으니 sent 는 아닙니다
    assert stored.conversation.stage == "meeting_link_sent"   # 단계는 옮겨집니다
    bookkeeping.assert_awaited_once()                         # HubSpot·워크북도 돕니다
    session.close()


# ---- Safe mode must not blind our own local bookkeeping ----------------------
# Safe mode blocks EXTERNAL side effects. It must still record what *we* did, or
# every elapsed-time view ("no reply for N days", reply rate, pipeline stage) is
# silently empty until go-live and the whole flow becomes untestable pre-launch.


@pytest.mark.asyncio
async def test_safe_mode_still_records_local_send_bookkeeping(safe, monkeypatch):
    """test_sent must still stamp last_outgoing_at/stage, and still run the sync.

    목적지별 차단은 guard_external_write 가 각자 합니다 — 여기서 두 번 막을 일이 아닙니다.
    """
    from unittest.mock import AsyncMock

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.agents import send_worker
    from src.db.base import Base
    from src.db.models import Contact, Conversation, Message

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
    monkeypatch.setattr(
        "src.integrations.senders.send",
        AsyncMock(side_effect=SendingDisabled("safe mode")),
    )
    bookkeeping = AsyncMock()
    monkeypatch.setattr(send_worker, "_post_send_bookkeeping", bookkeeping)

    assert await send_worker._send_one(mid) is True

    session.expire_all()
    stored = session.get(Message, mid)
    assert stored.status == "test_sent"          # safe mode marker
    assert stored.conversation.last_outgoing_at is not None   # the regression
    assert stored.conversation.stage == "meeting_link_sent"   # the regression
    bookkeeping.assert_awaited_once()            # 각 목적지는 guard_external_write 가 막습니다
    session.close()


# The matching guarantee for inbound — a customer answering a safe-mode ("test_sent")
# reply must still set Message.replied — is pinned by
# tests/test_inbound_flow.py::test_test_sent_reply_is_marked_answered_in_safe_mode,
# which already has the LLM/knowledge fixtures that path needs.
