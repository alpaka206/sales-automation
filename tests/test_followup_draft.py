"""New 이후의 회신도 초안을 만든다 (2026-09-07 운영자 지시).

설계는 ``docs/후속-회신-자동생성-설계.md`` 입니다. 이 파일이 고정하는 것은 그 문서의 3·4장
— **그 초안이 무엇에 답하는가**입니다. 나머지(언어·한국어 대역·승인)는 첫 회신과 같은
함수를 지나므로 이미 다른 파일이 고정하고 있습니다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents import inbound as inbound_module
from src.db.base import Base
from src.db.models import Contact, Conversation, CustomerInteraction, Message

BASE = datetime(2026, 9, 1, 0, 0, 0)


@pytest.fixture()
def thread(monkeypatch):
    """티켓 하나 — 문의 하나, 우리가 보낸 회신 하나. 둘 다 `messages` 에 있습니다."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(inbound_module, "SessionLocal", factory)
    with factory() as session:
        contact = Contact(
            normalized_email="buyer@acme.com", email="buyer@acme.com", full_name="Acme Buyer"
        )
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="negotiation", hubspot_ticket_id="1")
        session.add(conv)
        session.flush()
        session.add_all([
            Message(conversation_id=conv.id, direction="inbound", body="크레딧 가격이 궁금합니다",
                    status="received", created_at=BASE),
            Message(conversation_id=conv.id, direction="outgoing", body="미팅으로 안내드리겠습니다",
                    status="sent", created_at=BASE + timedelta(hours=1),
                    sent_at=BASE + timedelta(hours=1), hubspot_message_id="m-1"),
        ])
        session.commit()
        yield factory, conv.id, contact.id


def _interaction(factory, conv_id, contact_id, **kwargs):
    with factory() as session:
        session.add(CustomerInteraction(
            contact_id=contact_id, conversation_id=conv_id, channel="이메일", **kwargs
        ))
        session.commit()


# --------------------------------------------------------------------------- #
# 3장 — 대화를 어디서 읽는가
# --------------------------------------------------------------------------- #
def test_the_conversation_includes_what_hubspot_brought_back(thread):
    """**`messages` 만 보면 New 이후가 통째로 빕니다.**

    이 표에는 이 콘솔이 만든 것만 있습니다 — 최초 문의 하나와 우리가 여기서 보낸 회신.
    고객 답장과 허브스팟 화면에서 사람이 보낸 회신은 `customer_interactions` 에 있고,
    그것이 New 이후 대화의 전부입니다.
    """
    factory, conv_id, contact_id = thread
    _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:c-1",
                 direction="incoming", summary="언제 미팅이 가능한가요?",
                 happened_at=BASE + timedelta(hours=2))

    bodies = [turn.body for turn in inbound_module.thread_events(conv_id)]
    assert bodies == ["크레딧 가격이 궁금합니다", "미팅으로 안내드리겠습니다", "언제 미팅이 가능한가요?"]


def test_our_own_reply_is_not_counted_twice(thread):
    """수집기가 우리 회신을 허브스팟에서 도로 가져오므로 두 표에 다 있습니다.

    가르는 것은 짐작이 아니라 같은 id 입니다 — 발송 응답이 돌려준 스레드 메시지 id 가
    `messages.hubspot_message_id` 이고, 수집기는 그것으로 `external_id` 를 만듭니다.
    """
    factory, conv_id, contact_id = thread
    _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:m-1",
                 direction="outgoing", summary="미팅으로 안내드리겠습니다",
                 happened_at=BASE + timedelta(hours=1))

    assert len(inbound_module.thread_events(conv_id)) == 2


def test_a_reply_sent_from_hubspot_means_it_is_not_the_first_reply(thread, monkeypatch):
    """**금액 금지 가드가 엉뚱한 자리에 걸리던 이유입니다.**

    `messages` 만 세면 저쪽 화면에서 사람이 답한 티켓이 「첫 회신」으로 판정됩니다.
    """
    factory, conv_id, contact_id = thread
    with factory() as session:
        # 이 콘솔이 보낸 회신을 지웁니다 — 남는 것은 허브스팟에서 나간 회신뿐입니다.
        session.query(Message).filter(Message.direction == "outgoing").delete()
        session.commit()
    agent = inbound_module.InboundAgent.__new__(inbound_module.InboundAgent)
    assert agent._is_first_reply(conv_id) is True

    _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:x-1",
                 direction="outgoing", summary="영업이 자기 메일함에서 보낸 답",
                 happened_at=BASE + timedelta(hours=1))
    assert agent._is_first_reply(conv_id) is False


# --------------------------------------------------------------------------- #
# 4장 — 두 갈래
# --------------------------------------------------------------------------- #
def test_a_new_customer_message_is_what_the_follow_up_answers(thread):
    """마지막 회신 **뒤에** 온 고객 메시지가 있으면 그것에 답합니다."""
    factory, conv_id, contact_id = thread
    _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:c-2",
                 direction="incoming", summary="인도 루피로는 얼마인가요?",
                 happened_at=BASE + timedelta(hours=3))

    newer = inbound_module.latest_customer_message(conv_id)
    assert newer is not None and newer.body == "인도 루피로는 얼마인가요?"


def test_an_older_customer_message_does_not_count_as_a_new_one(thread):
    """회신 **전**에 온 말은 이미 답한 말입니다. 그걸 새 질문으로 읽으면 같은 답이 또 나갑니다."""
    factory, conv_id, contact_id = thread
    _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:c-0",
                 direction="incoming", summary="추가로 하나 더 여쭙니다",
                 happened_at=BASE + timedelta(minutes=30))

    assert inbound_module.latest_customer_message(conv_id) is None
    previous = inbound_module.last_sent_reply(conv_id)
    assert previous is not None and previous.body == "미팅으로 안내드리겠습니다"


def test_the_two_branches_reach_the_prompt(thread, monkeypatch):
    """지시문과 **답할 글**이 갈래마다 다릅니다.

    답장이 없을 때 지난 회신 본문을 같이 싣는 것이 요점입니다 — 그게 없으면 「이미 적은 말을
    되풀이하지 마라」가 지킬 수 없는 지시입니다.
    """
    factory, conv_id, contact_id = thread
    agent = inbound_module.InboundAgent.__new__(inbound_module.InboundAgent)
    seen: dict = {}

    class _LLM:
        def complete(self, name, fields, **kwargs):
            seen.update(fields)
            return inbound_module.DraftResult(subject="s", body="b", language="ko")

    agent.llm = _LLM()
    monkeypatch.setattr(inbound_module, "select_relevant_docs", lambda **kw: ("", None))
    monkeypatch.setattr(inbound_module, "korean_reading", lambda *a, **k: "", raising=False)
    contact_info = {
        "full_name": "Acme Buyer", "company": "Acme", "country": "IN",
        "last_message": "크레딧 가격이 궁금합니다", "email": "buyer@acme.com",
        "subject": "Pricing", "inquiry_language": "en",
    }
    classification = inbound_module.ClassifyResult(category="pricing_question", reasoning="")

    # ① 답장이 없다 → 같은 문의를 더 자세히, 지난 회신을 실어서
    agent._draft_reply(contact_info, classification, conv_id, "en")
    assert "고객의 답장은 아직 없습니다" in seen["followup_rule"]
    assert "미팅으로 안내드리겠습니다" in seen["followup_rule"]
    assert seen["last_message"] == "크레딧 가격이 궁금합니다"

    # ② 답장이 왔다 → 그 메시지에 답한다
    _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:c-9",
                 direction="incoming", summary="인도 루피로는 얼마인가요?",
                 happened_at=BASE + timedelta(hours=3))
    agent._draft_reply(contact_info, classification, conv_id, "en")
    assert "고객이 새로 보낸" in seen["followup_rule"]
    assert seen["last_message"] == "인도 루피로는 얼마인가요?"
