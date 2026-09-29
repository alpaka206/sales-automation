"""메일이 히스토리에 **두 번** 서거나 **빠지는** 자리 (2026-09-29 감사에서 재현하고 고친 것).

화면 셋(티켓 · 고객 · 수주)과 초안이 읽는 대화, 수집기 둘(허브스팟 스레드 · 개인 메일함), 손으로 적는
기록이 같은 메일을 한 번씩만 세고 하나도 안 빠뜨리는지.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.config import settings
from src.db.base import Base
from src.db.models import (
    Client,
    ClientContract,
    Contact,
    Conversation,
    CustomerInteraction,
    MailboxAccount,
    Message,
)

T0 = datetime(2026, 9, 21, 1, 0, 0)
REPLY = "안녕하세요 민하님,\n\n요청하신 견적서를 첨부드립니다.\n세부 조건은 미팅에서 말씀드리겠습니다."
INQUIRY = "안녕하세요. 영상 200분 더빙 견적을 받고 싶습니다. 영어→일본어입니다."


@pytest.fixture()
def db(monkeypatch):
    """모든 수집기·화면이 같은 메모리 DB 를 보게 — 한 모듈만 바꾸면 다른 모듈은 conftest DB 를 읽는다."""
    from src.agents import inbound, mailbox_sync, ticket_history
    from src.api.routes import customer_ops
    from src.db import session as db_session_module
    from src.integrations import gmail

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for module in (inbound, mailbox_sync, ticket_history, customer_ops, gmail, db_session_module):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(settings, "GOOGLE_TOKEN_ENCRYPTION_KEY",
                        "b3BlbnNlc2FtZW9wZW5zZXNhbWVvcGVuc2VzYW1lMTI=")
    return factory


def _contact_with(factory, *tickets: tuple[str, datetime]) -> tuple[int, list[int]]:
    with factory() as session:
        contact = Contact(normalized_email="buyer@acme.com", email="buyer@acme.com",
                          full_name="Acme Buyer", hubspot_contact_id="hc-1")
        session.add(contact)
        session.flush()
        ids = []
        for ticket_id, created in tickets:
            conv = Conversation(contact_id=contact.id, stage="meeting_link_sent",
                                hubspot_ticket_id=ticket_id, created_at=created)
            session.add(conv)
            session.flush()
            ids.append(conv.id)
        session.commit()
        return contact.id, ids


def _thread_row(external_id: str, direction: str, text: str, at: datetime) -> dict:
    """`collect_ticket_history` 가 돌려주는 한 줄과 같은 모양."""
    return {"external_id": external_id, "channel": "이메일", "direction": direction,
            "subject": "RE: 견적 문의", "summary": text, "handler": None, "happened_at": at}


def _summaries(records) -> list[str]:
    return [r["summary"] for r in records]


# --------------------------------------------------------------------------- #
# 1. 열쇠(hubspot_message_id) 없는 우리 회신 — 「발송 확인됨」·delivery_unknown — 이 두 번 선다
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", ["sent", "delivery_unknown"])
def test_a_reply_without_a_hubspot_id_is_shown_once(db, status):
    """복구 화면 「발송 확인됨」(`recovery.resolve_unknown_delivery`)은 상태만 `sent` 로 바꾸고
    `hubspot_message_id` 를 비워 둔다. 수집기가 같은 메일을 허브스팟 스레드에서 `hubspot:conv:<id>` 로
    가져오면 화면의 중복 거르기(열쇠 = hubspot_message_id)가 못 알아본다 — 티켓 화면 · 고객 상세 ·
    수주 고객 히스토리 · 다른 티켓 기록에 같은 회신이 두 번."""
    from src.db.history_view import ticket_records

    contact_id, (conv_id,) = _contact_with(db, ("T-1", T0))
    with db() as session:
        session.add(Message(conversation_id=conv_id, direction="outgoing", status=status,
                            channel="email", subject="RE: 견적 문의", body=REPLY,
                            sent_at=T0 + timedelta(hours=1)))
        # 허브스팟 사본 — HTML 을 글자로 푼 것이라 서명이 뒤에 붙어 있다.
        session.add(CustomerInteraction(
            contact_id=contact_id, conversation_id=conv_id, channel="이메일", direction="outgoing",
            summary=REPLY + "\n\nBest regards,\nPERSO AI", external_id="hubspot:conv:h-1",
            happened_at=T0 + timedelta(hours=1, seconds=4)))
        session.commit()
        records = ticket_records(session, contact_id, [conv_id])[conv_id]
    copies = [s for s in _summaries(records) if s.startswith("안녕하세요 민하님")]
    assert len(copies) == 1, f"같은 회신이 {len(copies)}번 보입니다"


def test_a_confirmed_reply_is_one_turn_in_the_conversation(db):
    """같은 원인이 초안이 읽는 대화(`inbound.thread_events`)에도 우리 회신을 두 번 싣는다."""
    from src.agents.inbound import thread_events

    contact_id, (conv_id,) = _contact_with(db, ("T-1", T0))
    with db() as session:
        session.add(Message(conversation_id=conv_id, direction="outgoing", status="sent",
                            channel="email", subject="RE: 견적 문의", body=REPLY,
                            sent_at=T0 + timedelta(hours=1)))
        session.add(CustomerInteraction(
            contact_id=contact_id, conversation_id=conv_id, channel="이메일", direction="outgoing",
            summary=REPLY, external_id="hubspot:conv:h-1",
            happened_at=T0 + timedelta(hours=1, seconds=4)))
        session.commit()
    outgoing = [t for t in thread_events(conv_id) if t.direction == "outgoing"]
    assert len(outgoing) == 1, f"우리 회신이 대화에 {len(outgoing)}번 실립니다"


# --------------------------------------------------------------------------- #
# 2. 최초 문의 — messages 의 문의 행과 스레드 수집이 넣는 사본이 둘 다 선다
# --------------------------------------------------------------------------- #
def test_the_first_inquiry_email_is_shown_once(db):
    """고객이 메일로 문의하면 접수(`inbound`)가 `messages` 문의 행을 만들고, 스레드 수집이 같은
    메일을 `hubspot:conv:` 줄로 넣는다. 문의 행에는 스레드 메시지 id 가 없어 안 걸러진다
    (`inbound.customer_turns_since` docstring 이 이 사본을 이미 안다 — 화면만 모른다)."""
    from src.db.history_view import ticket_records

    contact_id, (conv_id,) = _contact_with(db, ("T-1", T0))
    with db() as session:
        session.add(Message(conversation_id=conv_id, direction="inbound", status="received",
                            channel="email", subject="견적 문의", body=INQUIRY,
                            created_at=T0 + timedelta(seconds=40)))
        session.add(CustomerInteraction(
            contact_id=contact_id, conversation_id=conv_id, channel="이메일", direction="inbound",
            summary=INQUIRY, external_id="hubspot:conv:first", happened_at=T0))
        session.commit()
        records = ticket_records(session, contact_id, [conv_id])[conv_id]
    assert _summaries(records).count(INQUIRY) == 1, "같은 문의가 두 번 보입니다"


# --------------------------------------------------------------------------- #
# 3. 개인함 사본이 「가장 최근 티켓」에 붙고, 허브스팟 사본은 제 티켓에 붙으면 영영 안 접힌다
# --------------------------------------------------------------------------- #
def test_a_mailbox_copy_on_another_ticket_is_folded_into_the_thread_row(db):
    """고객 한 명에 티켓 A(옛것)·B(최신). 고객이 A 의 스레드로 답장 → perso.ai 사서함 사본이 먼저
    수집되면 `_newest_conversation` 이 B 에 붙인다. 뒤이어 스레드 수집이 A 에 `hubspot:conv:` 줄을 넣는데
    `_merge_crm_twins(A)` 는 **A 의 줄만** 봐서 B 의 `gmail:` 줄을 못 만난다 → 같은 메일이 두 줄,
    그중 하나는 남의 티켓(B)에. B 에서는 답장 판정(`advance_if_customer_replied`)까지 그 줄로 한다."""
    from src.agents.ticket_history import _store

    contact_id, (a_id, b_id) = _contact_with(db, ("T-A", T0), ("T-B", T0 + timedelta(days=30)))
    reply = "네, 그 조건으로 진행하겠습니다. 계약서 보내주세요."
    at = T0 + timedelta(days=40)
    with db() as session:
        session.add(CustomerInteraction(
            contact_id=contact_id, conversation_id=b_id, channel="이메일", direction="inbound",
            summary=reply, external_id="gmail:g-1", context="perso.ai@estsoft.com 개인 메일함",
            happened_at=at + timedelta(seconds=6)))
        session.commit()

    _store(a_id, contact_id, [_thread_row("hubspot:conv:m-1", "inbound", reply, at)])

    with db() as session:
        copies = session.scalar(select(func.count(CustomerInteraction.id)).where(
            CustomerInteraction.contact_id == contact_id, CustomerInteraction.summary == reply))
        on_b = session.scalar(select(func.count(CustomerInteraction.id)).where(
            CustomerInteraction.conversation_id == b_id))
    assert copies == 1, f"같은 답장이 {copies}줄"
    assert on_b == 0, "A 스레드의 답장이 B 티켓 기록에 서 있습니다"


# --------------------------------------------------------------------------- #
# 4. 지운 허브스팟 줄이 다음 수집에 되살아난다 (묘비는 gmail: 에만)
# --------------------------------------------------------------------------- #
def test_a_deleted_thread_row_does_not_come_back(db):
    """`customer_ops.interaction_delete` 는 「가져온 줄도 지운다」고 하면서 묘비는 `gmail:` 에만
    남긴다. `hubspot:conv:` 줄은 `_store` 가 `external_id` 로만 거르므로 다음 수집(웹훅 · 다시
    받기)에서 그대로 다시 선다 — 운영자가 치운 중복·오배정 줄이 되돌아온다."""
    from src.agents.ticket_history import _store
    from src.api.routes.customer_ops import interaction_delete

    contact_id, (conv_id,) = _contact_with(db, ("T-1", T0))
    row = _thread_row("hubspot:conv:m-9", "inbound", "잘못 붙은 메일", T0)
    _store(conv_id, contact_id, [dict(row)])
    with db() as session:
        interaction_id = session.scalar(select(CustomerInteraction.id).where(
            CustomerInteraction.external_id == "hubspot:conv:m-9"))
    asyncio.run(interaction_delete(contact_id, interaction_id, redirect_to=""))

    _store(conv_id, contact_id, [dict(row)])  # 다음 회차
    with db() as session:
        back = session.scalar(select(CustomerInteraction.id).where(
            CustomerInteraction.external_id == "hubspot:conv:m-9"))
    assert back is None, "지운 줄이 다음 수집에 되살아났습니다"


# --------------------------------------------------------------------------- #
# 5. 개인함 한 창에 50통이 넘으면 오래된 쪽이 영영 안 들어온다
# --------------------------------------------------------------------------- #
def test_mail_beyond_one_listing_page_is_not_lost(db, monkeypatch):
    """`_sync_one` 은 목록 한 장(`maxResults=50`, 지메일은 최신순)만 읽고 `nextPageToken` 을 안 보며,
    회차가 끝나면 `mark_polled` 가 창을 「지금」으로 민다. 서비스가 자는 동안(무료 플랜, 15분 무접속)
    공지·광고까지 50통이 넘게 쌓이면 **그 창의 오래된 고객 메일은 다음 회차 창 밖**이다 — 빠졌다는
    표시도 없다. (`test_the_window_is_since_we_last_looked_not_since_consent` 가 막았다고 적은 그 구멍.)"""
    from src.agents import mailbox_sync
    from src.integrations import gmail

    contact_id, _ = _contact_with(db, ("T-1", T0))
    with db() as session:
        session.add(MailboxAccount(email="untae@estsoft.com",
                                   encrypted_payload=gmail._encrypt({"refresh_token": "r"}),
                                   collect_from=datetime(2026, 9, 1)))
        session.commit()

    base = datetime(2026, 9, 10, tzinfo=timezone.utc)
    mails = [{
        "id": f"m{i}",
        "internalDate": str(int((base + timedelta(minutes=i)).timestamp() * 1000)),
        "labelIds": ["INBOX"], "snippet": f"고객 메일 {i}번 — 서로 다른 본문",
        "payload": {"headers": [
            {"name": "From", "value": "buyer@acme.com"},
            {"name": "To", "value": "untae@estsoft.com"},
            {"name": "Subject", "value": f"문의 {i}"},
            {"name": "Date", "value": (base + timedelta(minutes=i)).strftime("%a, %d %b %Y %H:%M:%S +0000")},
        ]},
    } for i in range(60)]

    class _Response:
        is_error = False
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    class _Gmail:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get(self, url, params=None):
            if url.endswith("/messages"):
                params = params or {}
                after = int(params["q"].split()[0].split(":", 1)[1])
                hits = sorted((m for m in mails if int(m["internalDate"]) // 1000 > after),
                              key=lambda m: -int(m["internalDate"]))
                cap = int(params.get("maxResults", 100))
                page = {"messages": [{"id": m["id"]} for m in hits[:cap]]}
                if len(hits) > cap:
                    page["nextPageToken"] = "next"
                return _Response(page)
            return _Response(next(m for m in mails if url.endswith("/" + m["id"])))

    monkeypatch.setattr(mailbox_sync, "access_token", lambda email: "t")
    monkeypatch.setattr(mailbox_sync.httpx, "Client", lambda **_k: _Gmail())
    monkeypatch.setattr(mailbox_sync, "enabled_accounts", lambda: ["untae@estsoft.com"])

    async def _no_note(*_a, **_k):
        return None

    async def _no_advance(*_a, **_k):
        return False

    monkeypatch.setattr(mailbox_sync, "_note_on_ticket", _no_note)
    monkeypatch.setattr("src.agents.ticket_history.advance_if_customer_replied", _no_advance)

    mailbox_sync.sync_mailboxes_once()
    mailbox_sync.sync_mailboxes_once()  # 다음 회차 — 창은 「방금 본 때 − 5분」부터

    with db() as session:
        got = session.scalar(select(func.count(CustomerInteraction.id)).where(
            CustomerInteraction.contact_id == contact_id,
            CustomerInteraction.external_id.like("gmail:%")))
    assert got == 60, f"60통 중 {got}통만 들어왔습니다 — 나머지는 다시 안 옵니다"


# --------------------------------------------------------------------------- #
# 6. 읽는 도중 온 메시지 — 웹훅의 「다시 받아라」가 수집기의 도장에 덮인다
# --------------------------------------------------------------------------- #
def test_a_message_that_arrives_while_the_ticket_is_being_read_stays_queued(db, monkeypatch):
    """예전 대기열은 `history_synced_at IS NULL` 하나였다. 수집기가 그 티켓을 읽는 **동안** 새 메시지가
    오면 웹훅의 「다시 받아라」(도장 지우기)가 할 일이 없었고 — 이미 NULL — 이어서 `_store` 가 「지금」을
    찍어 방금 온 메시지가 대기열에서 사라졌다. 이제 요청은 `history_requested_at`, 도장은 읽기 시작한
    때라 그 요청이 도장 뒤에 선다(이관 0128)."""
    from src.agents import ticket_history

    contact_id, (conv_id,) = _contact_with(db, ("T-1", T0))

    class _Client:
        async def close(self):
            return None

    async def _collect(client, ticket):
        rows = [_thread_row("hubspot:conv:m-1", "inbound", "첫 메일", T0)]
        # 여기서 고객의 두 번째 메일이 도착하고 웹훅이 온다 — 이미 읽은 목록에는 없다.
        ticket_history.mark_ticket_history_stale(conv_id)
        return rows

    async def _no_advance(_cid):
        return False

    monkeypatch.setattr("src.integrations.hubspot.HubSpotClient", _Client)
    monkeypatch.setattr(ticket_history, "collect_ticket_history", _collect)
    monkeypatch.setattr(ticket_history, "advance_if_customer_replied", _no_advance)
    asyncio.run(ticket_history.sync_one_ticket(conv_id))

    with db() as session:
        conv = session.get(Conversation, conv_id)
        assert conv.history_requested_at >= conv.history_synced_at, "읽는 도중 온 요청이 도장에 덮였습니다"

    # 다음 회차의 대기열에 그 티켓이 있다.
    picked: list[int] = []

    async def _record(conversation_id):
        picked.append(conversation_id)
        return 0

    monkeypatch.setattr(ticket_history, "sync_one_ticket", _record)
    asyncio.run(ticket_history.sync_pending_ticket_history())
    assert picked == [conv_id], "읽는 도중 온 메시지의 「다시 받아라」가 사라졌습니다"


# --------------------------------------------------------------------------- #
# 7. 수주 고객 화면 — 계약 소통 기록이 최근 200줄 밖이면 안 보인다
# --------------------------------------------------------------------------- #
def test_contract_notes_are_not_pushed_out_by_imported_chat(db):
    """`ui_won_customer` 는 그 연락처의 기록을 **최신 200줄로 자른 뒤** 내려보내고, 화면이
    `contract_seq` 로 거른다. 채팅·메일 수집 줄(실측 채팅만 896건)이 많은 고객은 계약 패널의
    손으로 적은 옛 기록이 「아직 기록이 없습니다」로 사라진다."""
    from src.api.routes.ui_api import ui_won_customer

    contact_id, (conv_id,) = _contact_with(db, ("T-1", T0))
    with db() as session:
        session.add(Client(client_id=1001, company="Acme", contact_id=contact_id))
        session.add(ClientContract(client_id=1001, seq=1))
        session.add(CustomerInteraction(
            contact_id=contact_id, channel="phone", direction="note", summary="1차 계약 킥오프 통화",
            contract_seq=1, happened_at=T0))
        session.add_all([CustomerInteraction(
            contact_id=contact_id, conversation_id=conv_id, channel="채팅", direction="inbound",
            summary=f"채팅 {i}", external_id=f"hubspot:conv:c{i}",
            happened_at=T0 + timedelta(days=1, minutes=i)) for i in range(200)])
        session.commit()

    payload = ui_won_customer(1001)
    seq_one = [c for c in payload["comms"] if c["contract_seq"] == 1]
    assert seq_one, "1차 계약 소통 기록이 화면에 안 내려갑니다"


# --------------------------------------------------------------------------- #
# 8. 「갔는지 모른다」던 발송은 허브스팟 사본이 보이면 저절로 닫힌다
# --------------------------------------------------------------------------- #
def test_an_unknown_send_is_settled_by_its_hubspot_copy(db, monkeypatch):
    """5xx · 타임아웃으로 `delivery_unknown` 이 된 회신 — 실제로는 나갔다. 스레드 수집이 그 사본을 가져오면
    나간 것으로 닫는다(복구 화면의 「발송 확인됨」과 같은 함수). 예전에는 사람이 누를 때까지 단계 ·
    `last_outgoing_at` · 발송 뒤 정리가 멈춰 있었고, 리마인더면 시퀀스가 통째로 섰다."""
    from src.agents.ticket_history import _store
    from src.api.routes import recovery

    monkeypatch.setattr(recovery, "SessionLocal", db)
    contact_id, (conv_id,) = _contact_with(db, ("T-1", T0))
    approved = T0 + timedelta(hours=1)
    with db() as session:
        conv = session.get(Conversation, conv_id)
        conv.stage = "new"
        # 승인 **전에** 선 같은 글(지난번 회신의 사본)은 이 발송의 사본이 아니다.
        session.add(Message(conversation_id=conv_id, direction="outgoing", status="delivery_unknown",
                            channel="email", subject="RE: 견적 문의", body=REPLY,
                            approved_at=approved, created_at=approved))
        session.commit()

    _store(conv_id, contact_id, [
        _thread_row("hubspot:conv:old", "outgoing", REPLY, approved - timedelta(hours=3)),
        _thread_row("hubspot:conv:h-7", "outgoing", REPLY + "\n\nBest regards,\nPERSO AI",
                    approved + timedelta(seconds=20)),
    ])

    with db() as session:
        message = session.scalar(select(Message).where(Message.conversation_id == conv_id))
        conv = session.get(Conversation, conv_id)
        assert message.status == "sent"
        assert message.hubspot_message_id == "h-7", "승인 뒤의 사본이 이 발송이다"
        assert message.post_send_sync_attempts == 0, "발송 뒤 정리가 처음부터 돈다"
        assert conv.last_outgoing_at is not None and conv.stage == "meeting_link_sent"


# --------------------------------------------------------------------------- #
# 9. 폼 문의 — 허브스팟 사본은 폼 칸을 늘어놓은 글이라 본문이 통째로 같지 않다
# --------------------------------------------------------------------------- #
def test_a_form_inquiry_is_shown_once(db):
    from src.db.history_view import ticket_records

    contact_id, (conv_id,) = _contact_with(db, ("T-1", T0))
    details = "We are looking for a tool that supports real-time voice translation for our team."
    with db() as session:
        session.add(Message(conversation_id=conv_id, direction="inbound", status="received",
                            channel="form", subject="Requirement", body=details,
                            created_at=T0 + timedelta(minutes=3)))
        session.add(CustomerInteraction(
            contact_id=contact_id, conversation_id=conv_id, channel="폼", direction="inbound",
            summary=("Service: AI Dubbing\nFull Name: Buyer\nEmail: buyer@acme.com\n"
                     f"Subject: Requirement\nDetails: {details}"),
            external_id="hubspot:conv:form-1", happened_at=T0))
        session.commit()
        records = ticket_records(session, contact_id, [conv_id])[conv_id]
    assert [r["source"] for r in records] == ["message"], "같은 폼 문의가 두 번 보입니다"


# --------------------------------------------------------------------------- #
# 10. 손으로 적은 기록의 시각 — 화면의 칸은 KST, 저장은 UTC
# --------------------------------------------------------------------------- #
def test_a_hand_entered_time_is_stored_as_utc():
    """`datetime-local` 칸은 운영자의 시계(KST)로 적히고 시간대 없이 온다. 그대로 저장하면 UTC 로 읽혀
    기록이 9시간 늦게 서고, 고칠 때마다 9시간씩 더 밀렸다(2026-09-29 운영 실측)."""
    from src.api.routes.customer_ops import _parse_form_time

    assert _parse_form_time("2026-09-02T19:14") == datetime(2026, 9, 2, 10, 14)
    assert _parse_form_time("2026-09-02T10:14:00+00:00") == datetime(2026, 9, 2, 10, 14)
    assert _parse_form_time("") is None


def test_a_note_time_without_a_zone_is_utc(monkeypatch):
    """허브스팟 노트의 `hs_timestamp` — 시간대 없는 값은 이 앱의 자(UTC)로 읽는다. 서버의 현지 시각으로
    읽으면 KST PC 에서 9시간 틀린다."""
    import json

    import httpx
    import respx

    from src.integrations.hubspot import BASE_URL, HubSpotClient

    with respx.mock:
        created = respx.post(f"{BASE_URL}/crm/v3/objects/notes").mock(
            return_value=httpx.Response(201, json={"id": "n-1"}))
        respx.put(url__regex=rf"{BASE_URL}/crm/v4/objects/notes/n-1/associations/default/.*").mock(
            return_value=httpx.Response(200, json={}))

        async def _run():
            client = HubSpotClient()
            try:
                await client.create_interaction_note("C-1", "본문", happened_at=datetime(2026, 9, 2, 10, 14))
            finally:
                await client.close()

        asyncio.run(_run())
    stamp = json.loads(created.calls[0].request.content)["properties"]["hs_timestamp"]
    assert int(stamp) == int(datetime(2026, 9, 2, 10, 14, tzinfo=timezone.utc).timestamp() * 1000)


# --------------------------------------------------------------------------- #
# 11. 끊긴 개인 메일함은 매일 여는 화면에 선다
# --------------------------------------------------------------------------- #
def test_a_broken_mailbox_is_on_the_dashboard(db):
    """한 사서함의 토큰이 09-22 에 죽었고 그 뒤 일주일 동안 그 메일이 안 들어왔다 — 이유는 설정 화면에만
    적혀 있었다(2026-09-29 운영 실측)."""
    from src.api.routes.ui_api import _broken_mailboxes

    with db() as session:
        session.add(MailboxAccount(email="untae@estsoft.com", encrypted_payload="x", enabled=True,
                                   last_error="invalid_grant"))
        session.add(MailboxAccount(email="perso.ai@estsoft.com", encrypted_payload="x", enabled=True))
        session.commit()
    assert [box["email"] for box in _broken_mailboxes()] == ["untae@estsoft.com"]
