"""scripts/cleanup_gmail_drafts.py — 보낸 메일로 들여온 지메일 초안을 지우는 한 번짜리 정리.

DB 만으로는 초안과 진짜 회신을 못 가르므로 줄마다 지메일에 묻는다. 그 물음은 `labels_of` 하나만 지나고,
여기서는 가짜를 넘긴다 — 이 테스트는 네트워크에 닿지 않는다.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scripts.cleanup_gmail_drafts import DRAFTS_FILTERED_SINCE, review
from src.db.base import Base
from src.db.models import Contact, CustomerInteraction, MailboxLinkDecision
from src.integrations.gmail import MailboxTokenError

BEFORE = DRAFTS_FILTERED_SINCE - timedelta(days=3)


@pytest.fixture()
def session(monkeypatch):
    import httpx

    def _no_network(*_a, **_k):
        raise AssertionError("이 정리는 테스트에서 네트워크에 닿으면 안 됩니다")

    monkeypatch.setattr(httpx, "Client", _no_network)
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)() as session:
        contact = Contact(normalized_email="buyer@acme.com", email="buyer@acme.com", full_name="Buyer")
        session.add(contact)
        session.flush()
        rows = [
            ("gmail:d1", "outgoing", "untae@estsoft.com 개인 메일함", None, BEFORE),
            ("gmail:r1", "outgoing", "perso.ai@estsoft.com 개인 메일함", None, BEFORE),
            ("gmail:x1", "outgoing", None, "untae@estsoft.com", BEFORE),  # 사서함은 보낸 주소로
            ("gmail:dead1", "outgoing", "gone@estsoft.com 개인 메일함", None, BEFORE),
            ("gmail:dead2", "outgoing", "gone@estsoft.com 개인 메일함", None, BEFORE),
            ("gmail:late", "outgoing", "untae@estsoft.com 개인 메일함", None, DRAFTS_FILTERED_SINCE + timedelta(hours=1)),
            ("gmail:in1", "inbound", "untae@estsoft.com 개인 메일함", None, BEFORE),
        ]
        for key, direction, context, handler, created in rows:
            session.add(CustomerInteraction(contact_id=contact.id, channel="이메일", direction=direction,
                                            summary=key, context=context, handler=handler, external_id=key,
                                            happened_at=created, created_at=created))
        session.commit()
        yield session


def _gmail(calls):
    labels = {"d1": {"DRAFT"}, "r1": {"SENT", "INBOX"}, "x1": None}

    def labels_of(mailbox, gmail_id):
        calls.append((mailbox, gmail_id))
        if mailbox == "gone@estsoft.com":
            raise MailboxTokenError("invalid_grant")
        return labels[gmail_id]

    return labels_of


def _ids(session, keys):
    found = {r.external_id: r.id for r in session.query(CustomerInteraction).all()}
    return [found[k] for k in keys]


def test_by_default_it_only_counts(session):
    calls: list = []
    expected = {
        "draft": _ids(session, ["gmail:d1"]), "kept": _ids(session, ["gmail:r1"]),
        "missing": _ids(session, ["gmail:x1"]), "unreadable": _ids(session, ["gmail:dead1", "gmail:dead2"]),
    }

    assert review(session, _gmail(calls)) == expected
    assert session.query(CustomerInteraction).count() == 7, "세기만 할 때는 아무것도 안 지운다"
    # 배포 뒤 줄 · 받은 메일은 묻지 않고, 죽은 사서함은 한 번 실패한 뒤 더 묻지 않는다.
    assert calls == [("untae@estsoft.com", "d1"), ("perso.ai@estsoft.com", "r1"),
                     ("untae@estsoft.com", "x1"), ("gone@estsoft.com", "dead1")]


def test_apply_deletes_only_drafts_and_leaves_a_tombstone(session):
    review(session, _gmail([]), apply=True)

    left = sorted(r.external_id for r in session.query(CustomerInteraction).all())
    assert left == ["gmail:dead1", "gmail:dead2", "gmail:in1", "gmail:late", "gmail:r1", "gmail:x1"]
    stone = session.get(MailboxLinkDecision, "gmail:d1")
    assert stone is not None and stone.decided_by == "gmail_draft", "묘비가 없으면 다음 수집이 되살린다"

