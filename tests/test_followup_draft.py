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
from src.agents.followup_sequence import REMINDER_NOTE_PREFIX
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
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)  # 운영 SessionLocal 과 같게
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


def _interaction(factory, conv_id, contact_id, channel="이메일", **kwargs):
    with factory() as session:
        session.add(CustomerInteraction(
            contact_id=contact_id, conversation_id=conv_id, channel=channel, **kwargs
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


def test_the_reminders_history_line_is_not_a_second_reply(thread):
    """발송 뒤 정리가 소통 히스토리에 남기는 「1차 리마인더 완료」는 **대화가 아닙니다.**

    2026-09-21 운영자 지시로 리마인더가 나가면 `customer_interactions` 에 한 줄이 남습니다 —
    그 표가 티켓 화면과 고객 상세가 그리는 목록이라 거기 있어야 눈에 보입니다. 그런데 그 줄은
    같은 리마인더를 **두 번째로** 그리는 것이고, 우리 회신을 거르는 `hubspot:conv:` 규칙에는
    열쇠가 달라 안 걸립니다.

    그대로 두면 셋이 깨집니다. ① `last_sent_reply` 가 그 일곱 글자를 「지난 회신」으로 집어
    실제 회신을 앵커에서 밀어냅니다(리마인더를 건너뛰는 규칙은 `messages` 쪽 표시에만
    걸립니다). ② 초안 프롬프트에 우리가 그렇게 답한 것처럼 실립니다. ③
    `latest_customer_message` 의 「마지막 회신 뒤」 기준선이 앞당겨져 직전에 온 고객 답장이
    안 보이게 됩니다.
    """
    factory, conv_id, contact_id = thread
    _interaction(factory, conv_id, contact_id,
                 external_id=f"{REMINDER_NOTE_PREFIX}77",
                 direction="outgoing", summary="1차 리마인더 완료",
                 happened_at=BASE + timedelta(hours=3))

    bodies = [turn.body for turn in inbound_module.thread_events(conv_id)]
    assert "1차 리마인더 완료" not in bodies
    assert bodies == ["크레딧 가격이 궁금합니다", "미팅으로 안내드리겠습니다"]

    # 그리고 「지난 회신」 앵커는 실제 회신 그대로입니다.
    last = inbound_module.last_sent_reply(conv_id)
    assert last is not None and last.body == "미팅으로 안내드리겠습니다"


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


def test_the_situation_block_states_facts_and_the_message_to_answer(thread, monkeypatch):
    """갈래마다 **사실**과 **답할 글**이 다릅니다 (2026-10-06 — 갈래별 지시문 대신 상황 블록).

    ① 우리 영업 메일 뒤로 고객 답장이 없다(`nudge`) — 최초 문의를 그대로 들고, 마지막으로 보낸 메일 본문과
    며칠 전인지를 싣습니다. 그 본문이 없으면 모델이 「이미 한 말」을 알 길이 없습니다.
    ② 답장이 왔다(`answer_reply`) — 그 메시지가 「가장 최근 문의」가 됩니다.
    어느 쪽에도 「더 자세히 써라」 · 「재촉하지 마라」 같은 코드의 영업 지시는 없습니다 — 그것은 콘솔 문서의 몫입니다.
    """
    import sys

    factory, conv_id, contact_id = thread
    agent = inbound_module.InboundAgent.__new__(inbound_module.InboundAgent)
    seen: dict = {}

    class _LLM:
        def complete(self, name, fields, **kwargs):
            seen.update(fields)
            return inbound_module.DraftResult(subject="s", body="b", language="ko")

    agent.llm = _LLM()
    monkeypatch.setattr(inbound_module, "select_relevant_docs", lambda **kw: ("", None))
    monkeypatch.setitem(sys.modules, "src.llm.precedents", None)
    contact_info = {
        "full_name": "Acme Buyer", "company": "Acme", "country": "IN",
        "last_message": "크레딧 가격이 궁금합니다", "email": "buyer@acme.com",
        "subject": "Pricing", "inquiry_language": "en",
    }
    classification = inbound_module.ClassifyResult(category="pricing_question", reasoning="")

    # ① 답장이 없다
    draft = agent._draft_reply(contact_info, classification, conv_id, "en")
    assert draft._context_manifest["situation"] == "nudge"
    assert "상황: nudge" in seen["situation"] and "고객의 답장이 아직 없습니다" in seen["situation"]
    assert "미팅으로 안내드리겠습니다" in seen["situation"], "지난 영업 메일 본문 — 고객이 이미 받은 글"
    assert "일 전(2026-09-01, UTC)" in seen["situation"]
    assert seen["last_message"] == "크레딧 가격이 궁금합니다"
    for advice in ("더 자세히", "재촉", "되풀이"):
        assert advice not in seen["situation"]
    assert "followup_rule" not in seen

    # ② 답장이 왔다 → 그 메시지에 답한다
    _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:c-9",
                 direction="incoming", summary="인도 루피로는 얼마인가요?",
                 happened_at=BASE + timedelta(hours=3))
    draft = agent._draft_reply(contact_info, classification, conv_id, "en")
    assert draft._context_manifest["situation"] == "answer_reply"
    assert "상황: answer_reply" in seen["situation"]
    assert seen["last_message"] == "인도 루피로는 얼마인가요?"


def test_the_situation_says_a_chatbot_or_cs_wrote_and_that_it_was_not_our_answer(no_console_reply, monkeypatch):
    """첫 회신인데 대화에 챗봇 답 · CS 안내가 있다 — 그 사실을 적고, 답할 글은 티켓의 문의입니다.

    챗봇과 주고받은 채팅 줄은 맥락이지 「가장 최근 문의」가 아닙니다 — 첫 회신의 기준선은 이제 봇 · CS 줄에서
    안 끊기므로(`role`), 채팅의 마지막 한마디를 답할 글로 고르면 그 한마디에 답합니다.
    """
    import sys

    factory, conv_id, contact_id = no_console_reply
    _handoff(factory, conv_id, contact_id)
    seen: dict = {}

    class _LLM:
        def complete(self, name, fields, **kwargs):
            seen.update(fields)
            return inbound_module.DraftResult(subject="s", body="b", language="ko")

    agent = inbound_module.InboundAgent.__new__(inbound_module.InboundAgent)
    agent.llm = _LLM()
    monkeypatch.setattr(inbound_module, "select_relevant_docs", lambda **kw: ("", None))
    monkeypatch.setitem(sys.modules, "src.llm.precedents", None)
    contact_info = {"full_name": "Acme Buyer", "company": "Acme", "country": "KR",
                    "last_message": "크레딧 가격이 궁금합니다", "subject": "[Chatbot] 문의 접수"}
    draft = agent._draft_reply(contact_info, inbound_module.ClassifyResult(category="pricing_question",
                                                                           reasoning=""), conv_id, "ko")

    assert draft._context_manifest["situation"] == "first_reply"
    assert "챗봇의 자동 답변이 있습니다 — 우리 영업의 답이 아닙니다" in seen["situation"]
    assert "CS 주소가 보낸 안내 메일이 있습니다 — 우리 영업의 답이 아닙니다" in seen["situation"]
    assert "마지막으로 보낸 이메일" not in seen["situation"], "우리 영업 메일이 없다"
    assert seen["last_message"] == "크레딧 가격이 궁금합니다"
    assert "엔터프라이즈 요금이 궁금해요" in seen["conversation_context"], "채팅 질문은 맥락으로 간다"


def test_a_customer_email_after_the_inquiry_is_what_a_first_reply_answers(no_console_reply, monkeypatch):
    """첫 회신이라도 고객이 문의 뒤에 메일로 정정하거나 더 물었으면 그 메일에 답합니다."""
    import sys

    factory, conv_id, contact_id = no_console_reply
    _handoff(factory, conv_id, contact_id)
    _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:mail-2", direction="incoming",
                 summary="정정: 100분이 아니라 300분입니다.", happened_at=BASE + timedelta(hours=1))
    seen: dict = {}

    class _LLM:
        def complete(self, name, fields, **kwargs):
            seen.update(fields)
            return inbound_module.DraftResult(subject="s", body="b", language="ko")

    agent = inbound_module.InboundAgent.__new__(inbound_module.InboundAgent)
    agent.llm = _LLM()
    monkeypatch.setattr(inbound_module, "select_relevant_docs", lambda **kw: ("", None))
    monkeypatch.setitem(sys.modules, "src.llm.precedents", None)
    contact_info = {"full_name": "Acme Buyer", "company": "Acme", "country": "KR",
                    "last_message": "크레딧 가격이 궁금합니다", "subject": ""}
    agent._draft_reply(contact_info, inbound_module.ClassifyResult(category="pricing_question", reasoning=""),
                       conv_id, "ko")

    assert seen["last_message"] == "정정: 100분이 아니라 300분입니다."


def test_reminder_does_not_answer_a_customer_correction(thread):
    factory, conv_id, contact_id = thread
    _interaction(factory, conv_id, contact_id, direction="incoming",
                 summary="정정합니다. 개인 구매가 아니라 기업 계약입니다.",
                 happened_at=BASE + timedelta(hours=2))
    with factory() as session:
        session.add(Message(conversation_id=conv_id, direction="outgoing", body="리마인더",
                            status="sent", prompt_variant="followup_reminder_1",
                            sent_at=BASE + timedelta(hours=3)))
        session.commit()
    latest = inbound_module.latest_customer_message(conv_id)
    assert latest is not None and "기업 계약" in latest.body


def test_test_sent_is_not_a_customer_visible_reply(thread):
    factory, conv_id, _ = thread
    with factory() as session:
        session.query(Message).filter(Message.direction == "outgoing").update({"status": "test_sent"})
        session.commit()
    agent = inbound_module.InboundAgent.__new__(inbound_module.InboundAgent)
    assert agent._is_first_reply(conv_id)
    assert inbound_module.last_sent_reply(conv_id) is None


def test_recent_correction_survives_a_long_derived_summary(thread):
    factory, conv_id, contact_id = thread
    with factory() as session:
        session.get(Conversation, conv_id).summary = "예전 정보입니다. " * 1000
        session.commit()
    _interaction(factory, conv_id, contact_id, direction="incoming",
                 summary="정정합니다. 구매는 20일 전입니다.", happened_at=BASE + timedelta(hours=2))
    agent = inbound_module.InboundAgent.__new__(inbound_module.InboundAgent)
    context = agent._build_conversation_context(conv_id, "추가 문의", max_chars=6000)
    assert "구매는 20일 전" in context
    assert len(context) <= 6000


def test_history_read_failure_is_not_an_empty_first_reply(monkeypatch):
    def unavailable():
        raise RuntimeError("db unavailable")
    monkeypatch.setattr(inbound_module, "SessionLocal", unavailable)
    with pytest.raises(RuntimeError):
        inbound_module.thread_events(123)


# --------------------------------------------------------------------------- #
# 누가 말했나 — 챗봇 답 · CS 안내는 우리 영업 회신이 아니다 (2026-10-06)
# --------------------------------------------------------------------------- #
# 운영 재생: 영업이 한 통도 안 보낸 티켓 42건이 「이미 답함」으로 읽혔다 — 채팅 봇 줄 28 · support@perso.ai
# 12 · CRM 2. 그 티켓의 첫 회신이 후속 회신 문서(단가표)를 받고 첫 회신 문서(모범 메일)를 잃었다(평가
# R415cs · R425chat). 방향은 그대로 「우리 쪽」이고(`classify_direction` 은 고정돼 있다), 갈리는 것은 역할이다.
@pytest.fixture()
def no_console_reply(thread, monkeypatch):
    """문의 하나뿐인 티켓 — 콘솔 회신을 지운다. CS 주소 목록은 개발자 `.env` 와 무관하게 기본값으로."""
    from src.common.config import settings

    monkeypatch.setattr(settings, "NON_SALES_SENDER_ADDRESSES", "support@perso.ai")
    factory, conv_id, contact_id = thread
    with factory() as session:
        session.query(Message).filter(Message.direction == "outgoing").delete()
        session.commit()
    return factory, conv_id, contact_id


def _handoff(factory, conv_id, contact_id):
    """고객이 채팅으로 묻고 봇이 답하고, CS 가 support@perso.ai 로 영업에 넘긴다는 메일을 보냈다."""
    _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:chat-q", channel="채팅",
                 direction="inbound", summary="엔터프라이즈 요금이 궁금해요",
                 happened_at=BASE + timedelta(minutes=10))
    _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:chat-bot", channel="채팅",
                 direction="outgoing", summary="담당자가 곧 연락드립니다!",
                 happened_at=BASE + timedelta(minutes=11))
    _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:cs-1", handler="support@perso.ai",
                 direction="outgoing", summary="영업팀에 전달했습니다. 곧 연락드리겠습니다.",
                 happened_at=BASE + timedelta(minutes=30))


def test_a_cs_handoff_and_chatbot_lines_are_not_our_first_reply(no_console_reply):
    factory, conv_id, contact_id = no_console_reply
    _handoff(factory, conv_id, contact_id)
    agent = inbound_module.InboundAgent.__new__(inbound_module.InboundAgent)

    events = inbound_module.thread_events(conv_id)
    assert {t.source_ref.split(":")[0] for t in events if t.direction == "outgoing"} == {"interaction"}
    assert agent._is_first_reply(conv_id) is True, "봇 · CS 줄로 「이미 답함」이 되면 첫 회신 문서를 잃는다"
    assert inbound_module.last_sent_reply(conv_id) is None
    # 봇 답 뒤에 고객 질문이 가려지지 않는다 — 가장 최근 고객 말은 채팅 질문이다.
    latest = inbound_module.latest_customer_message(conv_id)
    assert latest is not None and latest.body == "엔터프라이즈 요금이 궁금해요"
    assert sorted(t.role for t in events) == ["bot", "cs", "customer", "customer"]


@pytest.mark.parametrize("kind", ["console_sent", "hubspot_inbox_email", "logged_email_record"])
def test_a_real_sales_email_makes_it_a_follow_up(no_console_reply, kind):
    """영업이 실제로 보낸 이메일이면 어느 길로 왔든 후속 회신이다 — 콘솔 발송 · 허브스팟 화면에서 영업 주소로
    보낸 회신 · 손으로 적은 메일 기록(`email`, 보낸 사람 모름 — 거부 목록이라 영업으로 센다)."""
    factory, conv_id, contact_id = no_console_reply
    _handoff(factory, conv_id, contact_id)
    at = BASE + timedelta(hours=2)
    if kind == "console_sent":
        with factory() as session:
            session.add(Message(conversation_id=conv_id, direction="outgoing", body="견적 안내드립니다",
                                status="sent", created_at=at, sent_at=at, hubspot_message_id="m-9"))
            session.commit()
    elif kind == "hubspot_inbox_email":
        _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:sales-1",
                     handler="untae@estsoft.com", direction="outgoing", summary="견적 안내드립니다",
                     happened_at=at)
    else:
        _interaction(factory, conv_id, contact_id, channel="email", direction="outgoing",
                     summary="전화 후 메일로 견적 보냄", happened_at=at)
    agent = inbound_module.InboundAgent.__new__(inbound_module.InboundAgent)

    assert agent._is_first_reply(conv_id) is False
    previous = inbound_module.last_sent_reply(conv_id)
    assert previous is not None and previous.role == "sales" and previous.at == at


def test_the_cs_address_list_is_a_setting_not_code(no_console_reply, monkeypatch):
    """CS 별칭이 하나 더 생기면 설정에 더한다 — 코드에 박으면 그 주소의 안내가 조용히 「영업 회신」이 된다."""
    from src.common.config import settings

    factory, conv_id, contact_id = no_console_reply
    _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:help-1", handler="Help <help@perso.ai>",
                 direction="outgoing", summary="안내드립니다", happened_at=BASE + timedelta(hours=1))
    agent = inbound_module.InboundAgent.__new__(inbound_module.InboundAgent)
    assert agent._is_first_reply(conv_id) is False, "목록에 없는 우리 주소는 영업이다(거부 목록)"

    monkeypatch.setattr(settings, "NON_SALES_SENDER_ADDRESSES", "support@perso.ai, HELP@perso.ai")
    assert agent._is_first_reply(conv_id) is True


def test_the_context_says_who_spoke(no_console_reply):
    """모델이 보는 말머리 — 「우리」 하나면 챗봇 답과 CS 안내를 우리가 이미 보낸 영업 회신으로 읽는다."""
    factory, conv_id, contact_id = no_console_reply
    _handoff(factory, conv_id, contact_id)
    _interaction(factory, conv_id, contact_id, external_id="hubspot:conv:sales-2", handler="untae@estsoft.com",
                 direction="outgoing", summary="요금 안내드립니다", happened_at=BASE + timedelta(hours=3))
    agent = inbound_module.InboundAgent.__new__(inbound_module.InboundAgent)

    context = agent._build_conversation_context(conv_id, "다른 문의")
    assert "우리(영업) [" in context and "요금 안내드립니다" in context
    assert "우리(CS 안내) [" in context and "챗봇 [" in context
    assert "고객 [" in context and "고객 주장·미검증" in context
    assert "\n우리 [" not in context and not context.startswith("우리 [")
