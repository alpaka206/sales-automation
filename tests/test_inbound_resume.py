"""이어 쓰는 초안 — 리스가 끊겨 돌아온 New 초안 · 「초안 다시 쓰기」 · 「메일 발송」의 후속 초안 (2026-10-06).

셋은 같은 길(`InboundAgent.handle` + `_draft_message_id`)을 지나지만 같은 일이 아니다. 새 문의는 첫 초안
하나뿐이라, 새 문의에만 하는 일(워크북 행 · 언어 판별 · 유형 분류)을 나머지 둘이 되풀이하면 안 된다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents import inbound as inbound_module
from src.agents.inbound import ClassifyResult, DraftResult, InboundAgent
from src.db.base import Base
from src.db.models import Contact, Conversation, Message

T0 = datetime(2026, 9, 20, 1, 0)


class _LLM:
    """언어 판별은 `en`, 분류는 `purchase_inquiry` — 불렸는지는 `prompts` 가 적는다."""

    def __init__(self):
        self.prompts: list[str] = []

    def complete(self, name, fields=None, **kwargs):
        self.prompts.append(name)
        if "classify" in name:
            return ClassifyResult(category="purchase_inquiry", reasoning="")
        if "detect_language" in name:
            return "en"
        return "ok"


@pytest.fixture()
def world(monkeypatch):
    """운영 `SessionLocal` 처럼 autoflush=False. 워크북 두 함수는 부른 것만 적는다 — 시트에는 아무것도 안 간다."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(inbound_module, "SessionLocal", factory)
    sheet: list = []
    monkeypatch.setattr("src.agents.sheet_sync.reserve_inbound_client_id",
                        lambda conv_id: sheet.append(("reserve", conv_id)) or 1001)
    monkeypatch.setattr("src.integrations.google_sheets.record_inbound",
                        lambda record: sheet.append(("append", record["deal_stage"])))
    monkeypatch.setattr(inbound_module, "cache_korean_inquiries", lambda **kw: 0)
    monkeypatch.setattr(inbound_module, "notify_approval_once", lambda *a, **k: False)
    monkeypatch.setattr(inbound_module, "_default_signature", lambda: "signature_untae")
    monkeypatch.setattr("src.agents.inbound_poller._mark_ticket_processed", lambda ticket_id: None)

    llm = _LLM()
    agent = InboundAgent(llm=llm, hubspot=None)
    drafted: dict = {}

    def _draft(contact_info, classification, conv_id=None, inquiry_lang=None):
        drafted.update(language=inquiry_lang, category=classification.category)
        return DraftResult(subject="RE: quote", body="Hello", language=inquiry_lang or "ko")

    agent._draft_reply = _draft
    agent._enrich_draft_context = lambda info: None
    agent._company_type = lambda info: "확인 안 됨"
    agent._extract_requests = lambda *a: None
    return factory, agent, llm, sheet, drafted


def _ticket(factory, *, stage="new", language="pt", category="pricing_question", variant=None,
            body="", signature=None, sent_before=False) -> tuple[int, int]:
    """(대화 id, 이어 쓸 초안 id). 대화에는 첫 문의가 있고 언어 · 유형이 이미 저장돼 있다."""
    with factory() as session:
        contact = Contact(normalized_email="buyer@gmail.com", email="buyer@gmail.com", full_name="Buyer")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage=stage, hubspot_ticket_id="T-1",
                            inquiry_language=language, inquiry_category=category, inquiry_subject="Quote")
        session.add(conv)
        session.flush()
        session.add(Message(conversation_id=conv.id, direction="inbound", body="Please send a quote.",
                            status="received", created_at=T0))
        if sent_before:
            session.add(Message(conversation_id=conv.id, direction="outgoing", body="Our answer", status="sent",
                                created_at=T0 + timedelta(hours=1), sent_at=T0 + timedelta(hours=1)))
        draft = Message(conversation_id=conv.id, direction="outgoing", status="drafting", body=body,
                        prompt_variant=variant, signature_key=signature, target_language=language,
                        created_at=T0 + timedelta(days=1))
        session.add(draft)
        session.commit()
        return conv.id, draft.id


def _resume(agent, message_id, event_type):
    return agent.handle({
        "event_type": event_type, "object_id": "hs-1", "ticket_id": "T-1", "email": "buyer@gmail.com",
        "full_name": "Buyer", "last_message": "Please send a quote.", "subject": "Quote",
        "_draft_message_id": message_id,
    })


@pytest.mark.parametrize(("event_type", "variant", "mirrored"), [
    ("ticket_created", None, True),     # 리스가 끊겨 돌아온 New 첫 초안 — 앞 시도가 행을 못 남겼을 수 있다
    ("redraft", None, False),           # 「초안 다시 쓰기」
    ("redraft", "manual", False),       # 「메일 발송」의 후속 초안
    ("ticket_created", "manual", False),
])
def test_only_a_new_inquiry_reaches_the_workbook(world, event_type, variant, mirrored):
    """다시 쓰기 · 후속 초안이 워크북 append 를 다시 부르면, 시트 행이 없는 티켓(백필 300여 건)에 New ·
    Inquiry 행이 영업팀 공용 워크북에 선다 — 평가에서 후속 초안 F356 · F427 이 `record_inbound` 를 불렀다."""
    factory, agent, _llm, sheet, _drafted = world
    _conv, message_id = _ticket(factory, variant=variant, sent_before=variant == "manual",
                                stage="meeting_link_sent" if variant == "manual" else "new")

    result = _resume(agent, message_id, event_type)

    assert result["message_id"] == message_id
    assert sheet == ([("reserve", _conv), ("append", "New")] if mirrored else [])


def test_a_follow_up_keeps_the_threads_language_and_category_and_starts_signed(world):
    """후속 초안은 티켓 본문(최초 문의)을 다시 재지 않는다 — 언어는 대화에 저장된 그 칸이고(「첫 문의가 정하고
    그대로 둔다」), 유형은 다시 분류해 덮어쓰지 않는다. 서명은 첫 회신처럼 목록의 첫 서명으로 시작한다(평가의
    후속 26건이 전부 「서명 없음」이었다)."""
    factory, agent, llm, _sheet, drafted = world
    conv_id, message_id = _ticket(factory, stage="meeting_link_sent", variant="manual", sent_before=True)

    _resume(agent, message_id, "redraft")

    assert drafted == {"language": "pt", "category": "pricing_question"}
    assert "util/detect_language" not in llm.prompts and "inbound/classify" not in llm.prompts
    with factory() as session:
        msg = session.get(Message, message_id)
        conv = session.get(Conversation, conv_id)
        assert (msg.status, msg.target_language, msg.signature_key) == ("pending_approval", "pt", "signature_untae")
        assert conv.inquiry_category == "pricing_question"


def test_a_redraft_keeps_an_unsigned_choice_on_a_written_follow_up(world):
    """본문이 있는 후속 초안을 다시 쓰는 것이면 그 「서명 없음」은 운영자가 고른 것일 수 있다 — 안 바꾼다."""
    factory, agent, _llm, _sheet, _drafted = world
    _conv, message_id = _ticket(factory, stage="meeting_link_sent", variant="manual", sent_before=True,
                                body="운영자가 쓰던 글")

    _resume(agent, message_id, "redraft")

    with factory() as session:
        assert session.get(Message, message_id).signature_key is None


def test_a_conversation_with_no_stored_language_or_category_is_still_measured(world):
    """백필 티켓처럼 저장된 값이 없으면 예전처럼 재고 분류한다 — 빈 칸을 채우는 것은 덮어쓰기가 아니다."""
    factory, agent, llm, _sheet, drafted = world
    conv_id, message_id = _ticket(factory, stage="meeting_link_sent", variant="manual", sent_before=True,
                                  language=None, category=None)

    _resume(agent, message_id, "redraft")

    assert drafted == {"language": "en", "category": "purchase_inquiry"}
    assert "inbound/classify" in llm.prompts
    with factory() as session:
        assert session.get(Conversation, conv_id).inquiry_category == "purchase_inquiry"
