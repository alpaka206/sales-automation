"""나간 메일이 두 번 나가거나 기록이 빠지는 자리 — 발송 쪽 (2026-09-29 감사).

감사 재현(`tests/test_zz_audit_send.py`)에서 발송 워커 · 지메일 발송 · 발송 뒤 처리 몫을 옮겨 왔다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
import respx
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents import send_worker
from src.db.base import Base
from src.db.models import Contact, Conversation, ConversationProgress, Message
from src.integrations import gmail
from src.integrations.delivery import DeliveryPermanentError, DeliveryTransientError, DeliveryUnknown

MAILBOX = "gmail:untae@estsoft.com"


def _factory(session_class=Session):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=session_class, expire_on_commit=False)


def _approved(factory, *, stage="new", sheet_client_id=None, **fields) -> int:
    with factory() as session:
        contact = Contact(normalized_email="buyer@example.com", email="buyer@example.com",
                          full_name="Buyer", hubspot_contact_id="C-1")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage=stage, hubspot_ticket_id="T-1",
                            sheet_client_id=sheet_client_id)
        session.add(conv)
        session.flush()
        msg = Message(conversation_id=conv.id, direction="outgoing", status="approved",
                      body="Thanks for reaching out.", subject="RE: Quote",
                      to_address="buyer@example.com", **fields)
        session.add(msg)
        session.commit()
        return msg.id


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    send_worker._sent_timestamps.clear()
    send_worker._daily_count = 0
    # 발송 뒤 처리가 밖으로 나가는 자리들 — 기본은 조용히 성공. 테스트마다 필요한 것만 바꾼다.
    monkeypatch.setattr("src.integrations.hubspot.move_ticket_stage_after_send", lambda _t: True)
    monkeypatch.setattr("src.integrations.google_sheets.is_configured", lambda: False)
    monkeypatch.setattr(send_worker.settings, "HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS", False)
    monkeypatch.setattr("src.agents.ticket_history.mark_ticket_history_stale", lambda _c: None)
    yield
    send_worker._sent_timestamps.clear()
    send_worker._daily_count = 0


def _use(monkeypatch, factory):
    for target in ("src.agents.send_worker.SessionLocal", "src.db.session.SessionLocal",
                   "src.db.conversation_history.SessionLocal", "src.agents.summaries.SessionLocal"):
        monkeypatch.setattr(target, factory)


# ── 배달된 메일은 무슨 일이 있어도 send_failed 가 아니다 ──────────────────────────────────────

async def test_a_db_error_right_after_delivery_keeps_the_mail_delivered(monkeypatch):
    """허브스팟이 200 으로 받은 직후 commit 이 한 번 끊겼다. 예전에는 그것을 「알 수 없는 발송 실패 =
    영구」로 읽어 `send_failed` 를 찍었다(롤백으로 `hubspot_message_id` 도 사라졌다) — `approve` 는
    `send_failed` 를 「아무것도 안 갔다」로 보고 재발송을 받아 준다 → 고객이 같은 메일을 두 통."""
    state = {"fail": False}

    class FlakySession(Session):
        def commit(self):
            if state["fail"]:
                state["fail"] = False
                raise OperationalError("COMMIT", {}, Exception("server closed the connection"))
            return super().commit()

    factory = _factory(FlakySession)
    _use(monkeypatch, factory)
    mid = _approved(factory)
    assert send_worker._claim_id(mid)

    async def delivered(msg):
        msg.hubspot_thread_id = "th-1"
        msg.hubspot_message_id = "hub-1"
        state["fail"] = True  # 배달은 끝났다 — 다음 commit 이 끊긴다

    with patch("src.integrations.senders.send", delivered):
        assert await send_worker._send_one(mid)

    with factory() as session:
        row = session.get(Message, mid)
        assert row.status == "sent"
        assert row.hubspot_message_id == "hub-1"


async def test_when_the_delivery_cannot_be_written_at_all_it_waits_for_a_human(monkeypatch):
    """두 번 다 못 적으면 행은 우리가 잡은 그대로 남고 15분 뒤 `delivery_unknown` — 「실패」가 아니다."""
    state = {"fail": 0}

    class DeadSession(Session):
        def commit(self):
            if state["fail"]:
                state["fail"] -= 1
                raise OperationalError("COMMIT", {}, Exception("db gone"))
            return super().commit()

    factory = _factory(DeadSession)
    _use(monkeypatch, factory)
    mid = _approved(factory)
    assert send_worker._claim_id(mid)

    async def delivered(msg):
        msg.hubspot_message_id = "hub-1"
        state["fail"] = 2

    with patch("src.integrations.senders.send", delivered):
        await send_worker._send_one(mid)
    with factory() as session:
        assert session.get(Message, mid).status.startswith("sending:")
    assert send_worker._reclaim_stuck_sending(datetime.now(timezone.utc) + timedelta(hours=1)) == 1
    with factory() as session:
        assert session.get(Message, mid).status == "delivery_unknown"


# ── 지메일 발송의 결과 분류 — 허브스팟 발송과 같은 규칙 ─────────────────────────────────────────

@respx.mock
@pytest.mark.parametrize(("answer", "expected"), [
    (httpx.ReadTimeout("The read operation timed out"), DeliveryUnknown),
    (httpx.RemoteProtocolError("peer closed"), DeliveryUnknown),
    (httpx.Response(502, text="upstream"), DeliveryUnknown),
    (httpx.Response(200, content=b"<html>proxy</html>"), DeliveryUnknown),
    (httpx.Response(429, text="slow down"), DeliveryTransientError),
    (httpx.Response(400, json={"error": "invalid To"}), DeliveryPermanentError),
])
def test_a_mailbox_send_is_classified_like_the_hubspot_one(answer, expected):
    """타임아웃 · 5xx · 읽을 수 없는 2xx 는 **이미 나갔을 수 있다** — `send_failed` 가 되면 재승인이
    같은 메일을 한 번 더 보낸다. 429 만 다시 시도하고, 나머지 4xx 는 거절(아무것도 안 나갔다)."""
    route = respx.post(gmail.SEND_URL)
    if isinstance(answer, Exception):
        route.mock(side_effect=answer)
    else:
        route.mock(return_value=answer)
    with patch.object(gmail, "access_token", return_value="t"), pytest.raises(expected):
        gmail.send_mail("untae@estsoft.com", to="buyer@example.com", subject="RE: Quote",
                        html="<p>Thanks</p>")


@respx.mock
async def test_a_mailbox_send_that_gets_a_5xx_waits_for_a_human_and_rereads_the_thread(monkeypatch):
    factory = _factory()
    _use(monkeypatch, factory)
    mid = _approved(factory, channel_account_id=MAILBOX)
    reread: list[int] = []
    monkeypatch.setattr("src.agents.ticket_history.mark_ticket_history_stale", reread.append)
    monkeypatch.setattr("src.integrations.senders._validate_review", lambda *a, **k: None)
    respx.post(gmail.SEND_URL).mock(return_value=httpx.Response(502, text="upstream"))
    assert send_worker._claim_id(mid)

    with patch.object(gmail, "access_token", return_value="t"):
        await send_worker._send_one(mid)

    with factory() as session:
        row = session.get(Message, mid)
        assert row.status == "delivery_unknown", "5xx 는 갔는지 모른다 — 다시 보내면 두 통"
        # 나갔다면 허브스팟 스레드에 사본이 선다 — 그 티켓을 수집 대기열에 올려 스스로 확인되게.
        assert reread == [row.conversation_id]


# ── 개인 사서함 발송의 허브스팟 노트는 실패하면 다시 시도한다 ───────────────────────────────────

class _Notes:
    """허브스팟 노트 자리. `down` 이면 읽기부터 실패한다."""

    def __init__(self, existing=()):
        self.down = False
        self.existing = list(existing)
        self.created: list[str] = []

    def client(self):
        notes = self

        class _Client:
            async def ticket_notes(self, ticket_id):
                if notes.down:
                    raise httpx.ConnectError("hubspot unreachable")
                return notes.existing + notes.created

            async def create_interaction_note(self, contact_id, body, **_k):
                notes.created.append(body)

            async def _get_conversation_json(self, path, **_k):
                return {"results": []}  # 티켓 스레드 없음 — 개인함 발송은 스레드에 안 선다

            async def ticket_emails(self, ticket_id):
                return []

            async def close(self):
                return None

        return _Client


async def test_a_mailbox_send_whose_hubspot_note_failed_is_noted_later(monkeypatch):
    """개인 사서함 발송은 허브스팟 스레드에 없어서 티켓 노트가 유일한 허브스팟 기록이다. 예전에는 발송
    안에서 한 번 쓰고 실패를 삼켜, 허브스팟이 그 순간 느리면 노트가 영영 없었고 아무도 몰랐다."""
    factory = _factory()
    _use(monkeypatch, factory)
    mid = _approved(factory, stage="negotiation", channel_account_id=MAILBOX)
    notes = _Notes()
    notes.down = True
    monkeypatch.setattr("src.integrations.hubspot.HubSpotClient", notes.client())
    monkeypatch.setattr("src.integrations.gmail.send_mail", lambda *a, **k: "g-1")
    monkeypatch.setattr("src.integrations.senders._validate_review", lambda *a, **k: None)
    assert send_worker._claim_id(mid)
    await send_worker._send_one(mid)
    with factory() as session:
        row = session.get(Message, mid)
        assert row.status == "sent" and row.smtp_message_id == "g-1"
        assert "hubspot_note" in (row.post_send_sync_error or "")

    notes.down = False  # 허브스팟이 돌아왔다
    with factory() as session:
        session.get(Message, mid).post_send_sync_attempted_at = datetime.now(timezone.utc) - timedelta(hours=2)
        session.commit()
    await send_worker._retry_post_send_syncs()
    assert len(notes.created) == 1 and notes.created[0].startswith("[개인 메일함 untae@estsoft.com 에서 발송]")
    with factory() as session:
        assert session.get(Message, mid).post_send_synced_at is not None


async def test_the_mailbox_send_note_is_not_written_twice(monkeypatch):
    """이미 그 노트가 있으면(지난 회차에 적고 기록만 못 했다) 안 적는다 — 적기 전에 읽는다."""
    factory = _factory()
    _use(monkeypatch, factory)
    mid = _approved(factory, stage="negotiation", channel_account_id=MAILBOX)
    notes = _Notes(existing=[
        "[개인 메일함 untae@estsoft.com 에서 발송] RE: Quote\nThanks for reaching out.",
    ])
    monkeypatch.setattr("src.integrations.hubspot.HubSpotClient", notes.client())
    monkeypatch.setattr("src.integrations.gmail.send_mail", lambda *a, **k: "g-1")
    monkeypatch.setattr("src.integrations.senders._validate_review", lambda *a, **k: None)
    assert send_worker._claim_id(mid)
    await send_worker._send_one(mid)
    assert notes.created == []
    with factory() as session:
        assert session.get(Message, mid).post_send_synced_at is not None


# ── 단계 미러링은 이번 발송이 단계를 옮겼을 때만 ─────────────────────────────────────────────

async def _send(monkeypatch, factory, mid):
    async def delivered(msg):
        msg.hubspot_message_id = f"hub-{msg.id}"

    assert send_worker._claim_id(mid)
    with patch("src.integrations.senders.send", delivered):
        assert await send_worker._send_one(mid)


async def test_a_follow_up_on_a_contacted_ticket_does_not_put_the_stage_again(monkeypatch):
    """「지금 Contacted」는 「이번에 올렸다」가 아니다. 이미 Contacted 인 티켓에 나가는 후속 회신마다
    허브스팟 티켓을 Contacted 로 다시 PUT 하면, 그 사이 영업이 허브스팟에서 Negotiating 으로 옮긴 것을
    (우리 쪽이 아직 모르면) 되돌린다."""
    moves: list[str] = []
    monkeypatch.setattr("src.integrations.hubspot.move_ticket_stage_after_send",
                        lambda ticket: moves.append(ticket) or True)
    factory = _factory()
    _use(monkeypatch, factory)

    await _send(monkeypatch, factory, _approved(factory, stage="meeting_link_sent", prompt_variant="manual"))
    assert moves == []

    fresh = _factory()
    _use(monkeypatch, fresh)
    await _send(monkeypatch, fresh, _approved(fresh, stage="new"))
    assert moves == ["T-1"], "New 에서 올린 첫 회신은 허브스팟도 옮긴다"


async def test_a_retry_redoes_only_the_step_that_failed(monkeypatch):
    """워크북 한 칸이 실패했다고 재시도마다 허브스팟 단계를 다시 PUT 하지 않는다 — 몇 시간에 걸친
    여덟 번의 재시도 동안 영업이 옮긴 단계를 되돌릴 수 있었다."""
    moves: list[str] = []
    sheet_calls: list[int] = []
    monkeypatch.setattr("src.integrations.hubspot.move_ticket_stage_after_send",
                        lambda ticket: moves.append(ticket) or True)
    monkeypatch.setattr("src.integrations.google_sheets.is_configured", lambda: True)
    monkeypatch.setattr("src.integrations.google_sheets.update_inbound_stage",
                        lambda client_id, *_a: sheet_calls.append(client_id) and False)
    factory = _factory()
    _use(monkeypatch, factory)
    mid = _approved(factory, stage="new", sheet_client_id=1001)
    await _send(monkeypatch, factory, mid)
    assert moves == ["T-1"] and sheet_calls == [1001]

    with factory() as session:
        session.get(Message, mid).post_send_sync_attempted_at = datetime.now(timezone.utc) - timedelta(hours=2)
        session.commit()
    await send_worker._retry_post_send_syncs()
    assert moves == ["T-1"], "성공한 허브스팟 단계를 다시 PUT 했습니다"
    assert sheet_calls == [1001, 1001]


async def test_a_permanent_sync_error_is_not_retried_for_hours(monkeypatch):
    """워크북 설정이 없는 것(`not_configured` · `missing_client_id`)은 다시 해도 같다 — 재시도하지 않고
    오류는 행에 남겨 복구 화면에 뜨게 한다."""
    factory = _factory()
    _use(monkeypatch, factory)
    mid = _approved(factory, stage="new")
    await _send(monkeypatch, factory, mid)
    with factory() as session:
        row = session.get(Message, mid)
        assert row.post_send_sync_error == "google_sheets:not_configured"
        assert row.post_send_synced_at is None
        assert row.post_send_sync_attempts >= send_worker.POST_SEND_SYNC_MAX_RETRIES
        row.post_send_sync_attempted_at = datetime.now(timezone.utc) - timedelta(hours=2)
        session.commit()
    assert await send_worker._retry_post_send_syncs() == 0


# ── 첫 회차의 기록은 횟수와 같은 commit — 죽어도 빠지지도 두 번 적히지도 않는다 ─────────────────

async def test_first_attempt_records_survive_a_crash_and_are_written_once(monkeypatch):
    """예전에는 횟수를 먼저 commit 하고 그 뒤에 「답변 발송 완료」·요약 줄·리마인더 줄을 적었다. 그 사이에
    프로세스가 죽으면(메모리가 모자라 실제로 죽었다) 다음 회차는 「첫 회차가 아니다」로 읽어 그 줄들이
    영영 없었다."""
    state = {"die": False}

    class DyingSession(Session):
        def commit(self):
            super().commit()
            if state["die"]:  # commit 은 됐고, 그 직후 프로세스가 죽었다
                state["die"] = False
                raise OperationalError("COMMIT", {}, Exception("killed"))

    factory = _factory(DyingSession)
    _use(monkeypatch, factory)
    mid = _approved(factory, stage="new")
    with factory() as session:
        msg = session.get(Message, mid)
        msg.status, msg.sent_at = "sent", datetime.now(timezone.utc)
        session.commit()

    async def attempt():
        with factory() as session:
            msg = session.get(Message, mid)
            try:
                await send_worker._post_send_bookkeeping(
                    session, msg, session.get(Conversation, msg.conversation_id), mid
                )
            except OperationalError:
                session.rollback()

    state["die"] = True
    await attempt()  # 첫 회차 — 횟수를 commit 한 직후 죽었다
    await attempt()  # 재시도
    await attempt()  # 또 재시도 — 두 번 적으면 안 된다
    with factory() as session:
        rows = session.scalar(select(func.count(ConversationProgress.id)).where(
            ConversationProgress.kind == "reply"))
    assert rows == 1
