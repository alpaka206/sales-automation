"""선례 — 비슷한 상황에서 우리 팀이 실제로 보낸 메일을 고르고, 남의 고객을 가려 싣는다 (2026-10-06).

자료는 전부 지어낸 것이다. 운영 데이터의 글을 여기 옮기지 않는다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents import inbound as inbound_module
from src.db.base import Base
from src.db.models import Contact, Conversation, CustomerInteraction, Message
from src.llm import precedents
from src.llm.prompts import load_prompt

NOW = datetime.now(timezone.utc).replace(tzinfo=None)

INQUIRY = (
    "Hello, we need dubbing for 40 training videos (about 1,200 minutes) into Spanish.\n"
    "Our budget is $5,000. Call me at +1 415 555 0134 or write to jonas.berg@globex.example.\n"
    "Jonas Berg, Globex Inc"
)
FIRST_REPLY = (
    "Hi Jonas,\n\nThanks for reaching out about the Globex training videos.\n"
    "For 1,200 minutes we would suggest the Enterprise plan; each 30분 video uses 60 credits.\n"
    "We can deliver by October 30, 2026. Book a call here: https://calendly.example/perso/30min\n\n"
    "Best regards,\nRep Person\nSales | Perso AI\n+82 10 9999 8888"
)
ANSWER_REPLY = (
    "Hi Jonas,\n\nYes, Japanese is supported as well.\n\nKind regards,\nRep Person\n\n"
    "On Mon, Oct 5, 2026 at 10:00 AM Jonas Berg <jonas.berg@globex.example> wrote:\n"
    "> Can you also do Japanese?\n> We may need 3,000 minutes per year."
)


@pytest.fixture()
def crm(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)  # 운영 SessionLocal 과 같게
    # 선례는 `thread_events` 와 같은 세션 공장(`inbound.SessionLocal`)을 읽는다 — 초안 테스트가 바꾸는 그것.
    monkeypatch.setattr(inbound_module, "SessionLocal", factory)
    return factory


def _thread(session, email, name, company=None):
    contact = Contact(normalized_email=email or f"unknown:{name}", email=email, full_name=name,
                      company=company)
    session.add(contact)
    session.flush()
    conv = Conversation(contact_id=contact.id, stage="contacted")
    session.add(conv)
    session.flush()
    return contact, conv


def _message(session, conv, direction, body, days_ago, **kwargs):
    at = NOW - timedelta(days=days_ago)
    kwargs.setdefault("status", "sent" if direction == "outgoing" else "received")
    row = Message(conversation_id=conv.id, direction=direction, body=body, created_at=at,
                  sent_at=at if direction == "outgoing" else None, **kwargs)
    session.add(row)
    session.flush()
    return row


def _line(session, contact, conv, direction, summary, days_ago, channel="이메일", **kwargs):
    row = CustomerInteraction(contact_id=contact.id, conversation_id=conv.id, channel=channel,
                              direction=direction, summary=summary,
                              happened_at=NOW - timedelta(days=days_ago), **kwargs)
    session.add(row)
    session.flush()
    return row


@pytest.fixture()
def world(crm):
    """이번 티켓 하나와, 다른 고객 Globex 의 대화 하나 — 그 대화에 선례 셋과 선례가 아닌 줄들."""
    ids = {}
    with crm() as session:
        me, here = _thread(session, "maria@acme.example", "Maria Silva", "Acme Media")
        _message(session, here, "inbound", "How much is dubbing for our course?", 3)
        ids["here_reply"] = f"message:{_message(session, here, 'outgoing', 'Our reply to Maria', 2).id}"
        _message(session, here, "inbound", "And for twenty videos?", 1)
        # 같은 연락처의 다른 대화 — 이 고객 자신의 메일은 선례가 아니다.
        mine = Conversation(contact_id=me.id, stage="won")
        session.add(mine)
        session.flush()
        _message(session, mine, "inbound", "Earlier question from Maria", 40)
        ids["mine"] = f"message:{_message(session, mine, 'outgoing', 'Earlier answer to Maria', 39).id}"

        other, there = _thread(session, "jonas.berg@globex.example", "Jonas Berg", "Globex Inc")
        _message(session, there, "inbound", INQUIRY, 10)
        first = _message(session, there, "outgoing", FIRST_REPLY, 9, hubspot_message_id="hs-1")
        ids["first"] = f"message:{first.id}"
        # 그 회신의 허브스팟 사본 — 같은 메일이라 후보가 하나 더 생기면 안 된다.
        ids["copy"] = f"interaction:{_line(session, other, there, 'outgoing', FIRST_REPLY, 9, external_id='hubspot:conv:hs-1', handler='rep@estsoft.com').id}"
        ids["bot"] = f"interaction:{_line(session, other, there, 'outgoing', 'Bot: here is our pricing page', 8.5, channel='채팅', external_id='hubspot:conv:bot-1').id}"
        ids["cs"] = f"interaction:{_line(session, other, there, 'outgoing', 'CS: please check our help center', 8, external_id='hubspot:conv:cs-1', handler='support@perso.ai').id}"
        _line(session, other, there, "inbound", "Can you also do Japanese?\nWe may need 3,000 minutes per year.",
              7, external_id="hubspot:conv:in-2", handler="jonas.berg@globex.example")
        ids["answer"] = f"interaction:{_line(session, other, there, 'outgoing', ANSWER_REPLY, 6, external_id='hubspot:conv:out-2', handler='rep@estsoft.com').id}"
        reminder = _message(session, there, "outgoing", "Just checking in on our last email.", 4,
                            prompt_variant="followup_reminder_1")
        ids["reminder"] = f"message:{reminder.id}"
        ids["reminder_note"] = f"interaction:{_line(session, other, there, 'outgoing', 'Reminder Sent 1', 4, external_id=f'followup:reminder:{reminder.id}').id}"
        ids["nudge"] = f"interaction:{_line(session, other, there, 'outgoing', 'Hi Jonas, following up on the Japanese quote for Globex.', 2, external_id='hubspot:conv:out-3', handler='rep@estsoft.com').id}"
        ids["draft"] = f"message:{_message(session, there, 'outgoing', 'Unsent draft for Jonas', 1, status='pending_approval').id}"

        # 손 기록 · 옛 CRM 줄 — 영업 회신으로는 세지만 본문이 실제 메일 글이 아니라 선례가 아니다.
        noted_contact, noted = _thread(session, "lee@initech.example", "Lee Park", "Initech")
        _message(session, noted, "inbound", "Do you support Korean to English?", 20)
        ids["manual"] = f"interaction:{_line(session, noted_contact, noted, 'outgoing', '메일로 견적 안내함', 19, channel='email').id}"
        ids["crm"] = f"interaction:{_line(session, noted_contact, noted, 'outbound', 'Yes we do, here is how.', 18, channel='email', external_id='hubspot:email:77').id}"

        # 180일보다 오래된 회신, 사내 테스트 티켓.
        old_contact, old = _thread(session, "old@umbrella.example", "Old Customer", "Umbrella")
        _message(session, old, "inbound", "Old question", 201)
        ids["old"] = f"message:{_message(session, old, 'outgoing', 'Old answer', 200).id}"
        _, internal = _thread(session, "tester@estsoft.com", "Tester", "ESTsoft")
        _message(session, internal, "inbound", "test inquiry", 3)
        ids["internal"] = f"message:{_message(session, internal, 'outgoing', 'test answer', 3).id}"
        session.commit()
        ids["here"] = here.id
    return ids


class _Router:
    def __init__(self, ids=(), error: Exception | None = None):
        self.ids, self.error, self.calls = list(ids), error, []

    def complete(self, prompt_name, variables=None, **kwargs):
        self.calls.append((prompt_name, variables, kwargs))
        if self.error is not None:
            raise self.error
        return kwargs["schema"](ids=self.ids, reasoning="synthetic")


def _index(router: _Router) -> dict[str, str]:
    """인덱스의 {id: situation}."""
    text = router.calls[0][1]["index"]
    return dict(re.findall(r"- id: (\S+)\n  situation: (\S+)", text))


def _ask(world, router, **kwargs):
    kwargs.setdefault("situation", "first_reply")
    return precedents.precedents_for(world["here"], customer_text="How much is dubbing?",
                                     llm=router, **kwargs)


def test_only_sales_emails_to_other_customers_are_candidates(world):
    router = _Router()
    result = _ask(world, router)

    assert _index(router) == {
        world["first"]: "first_reply", world["answer"]: "answer_reply", world["nudge"]: "nudge",
    }
    assert result == {"text": "", "ids": [], "candidates": 3}  # 아무것도 안 맞으면 안 고르는 것이 정답
    index = router.calls[0][1]["index"]
    for excluded in ("here_reply", "mine", "copy", "bot", "cs", "reminder", "reminder_note", "draft", "manual",
                     "crm", "old", "internal"):
        assert world[excluded] not in index, excluded


def test_the_index_is_masked_and_one_line_per_mail(world):
    router = _Router()
    _ask(world, router)
    index = router.calls[0][1]["index"]

    for leaked in ("Jonas", "Globex", "jonas.berg@", "$5,000", "415 555", "calendly", "1,200", "3,000"):
        assert leaked not in index, leaked
    assert "[고객]" in index and "[금액]" in index


def test_the_router_is_one_flash_call_with_room_for_thinking(world):
    router = _Router()
    _ask(world, router, situation="answer_reply")

    (prompt_name, variables, kwargs), = router.calls
    assert prompt_name == "inbound/select_precedents"
    assert kwargs["tier"] == "flash" and kwargs["max_tokens"] >= 256  # `tests/test_llm_budgets.py`
    assert kwargs["schema"] is precedents.SelectPrecedentsResult
    assert variables["situation"] == "answer_reply" and variables["limit"] == 2
    prompt = load_prompt("inbound/select_precedents", variables)
    assert "{{" not in prompt and "untrusted" in prompt and variables["index"] in prompt


def test_the_chosen_precedents_are_rendered_masked_without_quotes_or_signature(world):
    router = _Router([world["first"], world["answer"]])
    result = _ask(world, router)

    text = result["text"]
    assert result["ids"] == [world["first"], world["answer"]]
    assert text.startswith(precedents.HEADER)
    assert "그 고객의 것" in text and "회사 문서" in text
    assert "### 사례 1 · 첫 회신" in text and "### 사례 2 · 고객 답장에 대한 회신" in text
    for placeholder in ("[고객]", "[회사]", "[이메일]", "[전화]", "[금액]", "[날짜]", "[링크]", "[수량]"):
        assert placeholder in text, placeholder
    for leaked in ("Jonas", "Globex", "globex.example", "5,000", "415 555", "1,200", "October 30",
                   "calendly", "9999"):
        assert leaked not in text, leaked
    # 작은 수는 제품 사실이라 남는다.
    assert "30분" in text and "60 credits" in text
    # 인용된 이전 메일과 서명 블록은 뗀다 — 맺음말은 남는다.
    assert "Best regards," in text and "Kind regards," in text
    assert "Rep Person" not in text and "Sales | Perso AI" not in text
    assert "wrote:" not in text and "> Can you also" not in text


def test_the_limit_is_respected_and_unknown_ids_are_ignored(world):
    router = _Router(["message:999999", world["nudge"], world["nudge"], world["first"], world["answer"]])
    result = _ask(world, router, limit=2)

    assert result["ids"] == [world["nudge"], world["first"]]
    assert "### 사례 2" in result["text"] and "### 사례 3" not in result["text"]
    assert "고객 답이 없어 다시 보낸 메일" in result["text"]


def test_a_router_failure_means_no_precedents(world):
    result = _ask(world, _Router(error=RuntimeError("model down")))

    assert result == {"text": "", "ids": [], "candidates": 3}


def test_a_history_read_failure_means_no_precedents(world, monkeypatch):
    def broken(conv_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(precedents, "_candidates", broken)
    router = _Router([world["first"]])
    assert _ask(world, router) == {"text": "", "ids": [], "candidates": 0}
    assert router.calls == []


def test_no_candidates_means_no_model_call(crm):
    with crm() as session:
        _, alone = _thread(session, "solo@nowhere.example", "Solo Person")
        _message(session, alone, "inbound", "Hello?", 1)
        session.commit()
        conv_id = alone.id
    router = _Router()

    assert precedents.precedents_for(conv_id, customer_text="Hello?", situation="first_reply",
                                     llm=router) == {"text": "", "ids": [], "candidates": 0}
    assert router.calls == []


def test_masking_names_emails_links_amounts_dates_and_long_numbers():
    text = (
        "Hi Maria,\n김철수님 안녕하세요. Maria Silva from ACME Media (maria.silva@acme.example) asked on "
        "2026-10-06 and Oct 6, 2026 (2026년 10월 6일). See https://www.acme.example/brief?id=7 or "
        "www.acme.example. Call +82 10-1234-5678. Budget $1,200.50/month, 3만원, ₩99,000, USD 300, "
        "29€, 50 dollars. Volume 12,000 minutes, 3만 분, 10k views, order 98765. "
        "Each 30분 video uses 60 credits; refunds within 14일; Tier 2."
    )
    masked = precedents.mask(text, name="Maria Silva", company="ACME Media Ltd.")

    for leaked in ("Maria", "Silva", "ACME", "김철수", "acme.example", "2026", "Oct 6", "10월",
                   "1234-5678", "1,200", "3만", "99,000", "300", "29€", "50 dollars", "12,000",
                   "10k", "98765"):
        assert leaked not in masked, leaked
    for kept in ("30분", "60 credits", "14일", "Tier 2"):
        assert kept in masked, kept
    assert masked.startswith("Hi [고객],\n[고객]님")
    assert "[회사]" in masked and "[이메일]" in masked and "[링크]" in masked and "[전화]" in masked


@pytest.mark.parametrize("mail", [
    "Obrigado!\n\nEm qui., 24 de set. de 2026 22:39, Ana Lima <\nana@example.org> escreveu:\n> antes",
    "Thanks!\n\nOn Thu, Sep 24, 2026 at 10:39 PM Ana Lima <ana@example.org>\nwrote:\n> before",
    "Спасибо!\n\n24 сент. 2026 г., в 22:39, Ana Lima <ana@example.org> написал(а):\n\n> раньше",
    "<html><head><style>p{color:red}</style></head><body><p>Thanks!</p>"
    "<div>On Thu, Sep 24, 2026 at 10:39 PM Ana &lt;ana@example.org&gt; wrote:</div></body></html>",
    "감사합니다.\n\n2026년 9월 24일 (목) 오후 10:39, 홍길동 <hong@example.org>님이 작성:\n> 이전",
    "감사합니다.\n\n2026년 9월 24일 (목) 오후 10:39, 홍길동 <hong@example.org>님이\n작성:\n> 이전",
])
def test_quoted_history_and_markup_are_dropped(mail):
    """인용 머리는 줄바꿈되기도 하고(시각이 든 앞줄), 언어마다 말이 다르고, HTML 원문으로 오기도 한다."""
    cleaned = precedents._clean(mail)

    assert cleaned in {"Obrigado!", "Thanks!", "Спасибо!", "감사합니다."}


def test_a_body_line_that_looks_like_a_header_is_kept():
    body = "안녕하세요.\n1. 신청서 작성: 첨부 양식\n2. As my colleague wrote: see below\n3. 회신"

    assert precedents._clean(body) == body


def test_name_particles_are_not_masked_as_names():
    masked = precedents.mask("Olá Ana, desde 24 de set. o plano da Ana de Souza.", name="Ana de Souza")

    assert "24 de set." in masked and "plano da [고객]" in masked and "Souza" not in masked


def test_a_placeholder_contacts_name_is_a_ticket_subject_not_a_name(crm):
    """주소 없는 자리 표시 연락처의 이름 칸은 티켓 제목이다 — 그 낱말들을 가리면 본문이 [고객] 투성이가 된다."""
    with crm() as session:
        _, here = _thread(session, "maria@acme.example", "Maria Silva")
        placeholder, there = _thread(session, None, "Dubbing quote request")
        _line(session, placeholder, there, "inbound", "Need a dubbing quote", 5, external_id="hubspot:conv:p-1")
        reply = _line(session, placeholder, there, "outgoing", "Thanks, our dubbing quote follows.", 4,
                      external_id="hubspot:conv:p-2", handler="rep@estsoft.com")
        session.commit()
        conv_id, reply_ref = here.id, f"interaction:{reply.id}"
    result = precedents.precedents_for(conv_id, customer_text="quote?", situation="first_reply",
                                       llm=_Router([reply_ref]))

    assert "our dubbing quote follows" in result["text"]


def test_candidates_are_read_in_a_few_queries_not_one_round_trip_set_per_conversation(crm):
    """대화마다 읽으면 후보 60건에 왕복이 수백 번이었다(운영 Postgres) — 묶음으로 읽어 몇 번에 끝난다."""
    from sqlalchemy import event

    with crm() as session:
        _, here = _thread(session, "maria@acme.example", "Maria Silva", "Acme Media")
        _message(session, here, "inbound", "How much is dubbing?", 1)
        for number in range(30):
            _, there = _thread(session, f"buyer{number}@example{number}.test", f"Buyer {number}")
            _message(session, there, "inbound", f"Question number {number} about dubbing", 5)
            _message(session, there, "outgoing", f"Answer number {number}: yes, we can.", 4)
        session.commit()
        conv_id = here.id

    statements = []
    engine = crm.kw["bind"]
    listener = lambda *args: statements.append(args[2])  # noqa: E731 — (conn, cursor, statement, …)
    event.listen(engine, "before_cursor_execute", listener)
    try:
        router = _Router()
        result = precedents.precedents_for(conv_id, customer_text="How much is dubbing?",
                                           situation="first_reply", llm=router)
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert result["candidates"] == 30
    assert len(statements) <= 10, statements
