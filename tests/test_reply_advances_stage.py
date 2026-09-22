"""고객 답장 → Contacted 에서 협의 중으로 — **들어오는 길 셋이 같은 판단을 부른다**.

2026-09-22 운영자 보고: 「수신은 왔는데 stage 가 안 넘어가졌어」. 판단은
`ticket_history.advance_if_customer_replied` 한 곳이고(그 규칙 자체는
`tests/test_ticket_history.py` 가 고정한다), 여기는 개인함 수집이 그것을 **부르는지**를 본다 —
그 길로 들어온 답장은 허브스팟 스레드를 안 지나므로 그쪽 수집기가 영영 못 본다. 운영자의
「수신」 기록 쪽은 `tests/test_interaction_log.py` 에 있다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents import mailbox_sync, ticket_history
from src.db.base import Base
from src.db.models import Contact, Conversation, MailboxAccount
from src.integrations import gmail


def _one_inbound_mail(monkeypatch, *, sender: str):
    class _Response:
        is_error = False
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    mail = {"id": "m1", "internalDate": "1789000000000", "snippet": "좋습니다, 진행하죠.",
            "payload": {"headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "buyer@acme.com" if sender != "buyer@acme.com" else "untae@estsoft.com"},
                {"name": "Subject", "value": "Re: 견적"},
                {"name": "Date", "value": "Tue, 22 Sep 2026 10:00:00 +0900"},
            ]}}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get(self, url, params=None):
            if url.endswith("/messages"):
                return _Response({"messages": [{"id": "m1"}]})
            return _Response(mail)

    monkeypatch.setattr(mailbox_sync, "access_token", lambda email: "t")
    monkeypatch.setattr(mailbox_sync.httpx, "Client", lambda **_k: _Client())


def _db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(mailbox_sync, "SessionLocal", factory)
    monkeypatch.setattr(gmail, "SessionLocal", factory)
    with factory() as session:
        contact = Contact(normalized_email="buyer@acme.com", email="buyer@acme.com", full_name="Buyer")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="meeting_link_sent", hubspot_ticket_id="T-1")
        session.add(conv)
        session.add(MailboxAccount(email="untae@estsoft.com", encrypted_payload="x",
                                   collect_from=datetime(2026, 9, 1)))
        session.commit()
        return conv.id


def _watch(monkeypatch) -> list[int]:
    asked: list[int] = []

    async def _advance(conversation_id):
        asked.append(conversation_id)
        return True

    monkeypatch.setattr(ticket_history, "advance_if_customer_replied", _advance)
    return asked


def test_a_customer_mail_in_a_personal_mailbox_asks_the_stage_rule(monkeypatch):
    conv_id = _db(monkeypatch)
    asked = _watch(monkeypatch)
    _one_inbound_mail(monkeypatch, sender="buyer@acme.com")

    assert mailbox_sync.sync_mailboxes_once() == {"added": 1}
    assert asked == [conv_id]


def test_our_own_mail_in_a_personal_mailbox_does_not(monkeypatch):
    """우리가 보낸 것은 Contacted 를 만든 사건이지 답장이 아니다 — 판단을 부를 이유가 없다."""
    _db(monkeypatch)
    asked = _watch(monkeypatch)
    _one_inbound_mail(monkeypatch, sender="untae@estsoft.com")

    assert mailbox_sync.sync_mailboxes_once() == {"added": 1}
    assert asked == []


def test_a_failing_stage_rule_does_not_lose_the_sweep(monkeypatch):
    """단계는 장부다. 판정이 터져도 줄은 이미 들어갔고 회차는 성공이다."""
    _db(monkeypatch)
    _one_inbound_mail(monkeypatch, sender="buyer@acme.com")

    async def _boom(conversation_id):
        raise RuntimeError("stage sync down")

    monkeypatch.setattr(ticket_history, "advance_if_customer_replied", _boom)
    assert mailbox_sync.sync_mailboxes_once() == {"added": 1}
