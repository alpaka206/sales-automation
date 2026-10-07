"""초안은 정리된 콘솔 지식을 따른다 (2026-10-06, WP-D).

지키는 것:

* 회사 문서는 이번 상황(first_reply · answer_reply · nudge)에 맞게 정리돼 **초안 호출의 system** 으로 간다
  (`organizer.knowledge_for`). 정리기가 없거나 실패하면 예전처럼 문서 통째로 — 빈 문맥이 아니다.
* 본문은 모델이 쓴 이메일 그대로다. 코드는 영업 지시를 하지 않고 상황을 사실로만 적는다.
* 제목은 `choose_reply_subject` 가 고른다 — 이어지는 이메일 스레드의 제목, 없으면 검사를 지난 모델 제안 ·
  고객이 폼에 쓴 제목 · 기본 제목. 허브스팟 티켓 이름은 후보가 아니다.
* 매니페스트에는 코드 · 개수 · id 만 — 고객 글도 문서 글도 안 남는다.
* 지난 메일 참고(`llm.precedents`)는 없어도 · 실패해도 초안이 선다.

본문은 전부 지어낸 것이다.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import types
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import src.db.email_templates  # noqa: F401 — 픽스처가 SessionLocal 을 바꾸기 전에 (test_inbound_flow 참고)
from src.agents import inbound
from src.agents.draft_evidence import DraftEvidenceError
from src.common.subjects import choose_reply_subject, valid_subject
from src.db.models import Contact, Conversation, CustomerInteraction, Event, Message, PolicySource
from src.llm import organizer, precedents
from src.llm.policy_context import PolicyContextError, PolicySnapshot
from src.llm.pricing import LLMResult
from src.llm.prompts import load_prompt

NOW = datetime.utcnow()

DOC = (
    "# 응대 규칙\n"
    "\n"
    "## §1. 공통 규칙\n"
    "- 고객이 준 정보는 다시 묻지 않는다.\n"
    "- **Lip-sync** is available for all target languages.\n"
    "\n"
    "## §2. 첫 회신 예시\n"
    "Hi there, thanks for reaching out about FIRST_EXAMPLE.\n"
    "\n"
    "## §3. 무응답 리마인드\n"
    "Just following up on NUDGE_TEMPLATE.\n"
    "\n"
    "## §4. 내부 단가\n"
    "| 등급 | 분당 단가 | 원가 |\n"
    "|---|---|---|\n"
    "| 기본 | $9.10 | $3.30 |\n"
)
_LABELS = (
    ("공통 규칙", {"kind": "rule", "applies_to": ["any"]}),
    ("첫 회신 예시", {"kind": "example", "applies_to": ["first_reply"]}),
    ("무응답 리마인드", {"kind": "template", "applies_to": ["nudge"]}),
    ("내부 단가", {"kind": "internal_price", "applies_to": ["answer_reply"]}),
)


class _Labeler:
    """`policy/organize` 대신 — 제목 경로의 낱말로 꼬리표를 고른다."""

    def complete(self, prompt_name, variables=None, schema=None, **kwargs):
        sections = []
        for index, path in re.findall(r"^=== section (\d+) · (.*?) ===$", variables["sections"], re.M):
            label = {"kind": "other", "applies_to": ["any"]}
            for word, chosen in _LABELS:
                if word in path:
                    label = chosen
            sections.append({"idx": int(index), **label})
        return schema.model_validate({"sections": sections})


@pytest.fixture()
def draft_db(db_session_factory, monkeypatch):
    monkeypatch.setattr("src.db.session.SessionLocal", db_session_factory)
    monkeypatch.setattr(inbound, "SessionLocal", db_session_factory)
    monkeypatch.setitem(sys.modules, "src.llm.precedents", None)  # 지난 메일 참고 없이 — 따로 고정합니다
    return db_session_factory


def _conversation(factory, *, sales_reply=False, customer_reply=False, doc=DOC, organized=False):
    with factory() as session:
        source = PolicySource(label="응대 규칙", title="응대 규칙", doc_key="rules", mode="rules", body=doc)
        session.add(source)
        session.add(PolicySource(label="기밀", doc_key="secret", mode="rules", model_access="human_only",
                                 body="PRIVATE_POLICY_SENTINEL"))
        contact = Contact(normalized_email="ana@example.test", email="ana@example.test", full_name="Ana Customer")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="new", hubspot_ticket_id="T-1", inquiry_language="en")
        session.add(conv)
        session.flush()
        session.add(Message(conversation_id=conv.id, direction="inbound", channel="email",
                            subject="[Form] 견적 > 엔터프라이즈 전달", body="We need 40 videos dubbed.",
                            status="received", created_at=NOW - timedelta(days=6)))
        if sales_reply:
            session.add(Message(conversation_id=conv.id, direction="outgoing", channel="email",
                                subject="Dubbing your training videos", body="Here is how it works.",
                                status="sent", created_at=NOW - timedelta(days=5), sent_at=NOW - timedelta(days=5)))
        if customer_reply:
            session.add(CustomerInteraction(contact_id=contact.id, conversation_id=conv.id, channel="이메일",
                                            direction="incoming", summary="How long does it take?",
                                            subject="Re: Dubbing your training videos",
                                            external_id="hubspot:conv:r-1", happened_at=NOW - timedelta(days=1)))
        if organized:
            organizer.organize_source(session, source, llm=_Labeler())
        session.commit()
        return conv.id, source.id


INFO = {"full_name": "Ana Customer", "company": "Acme", "country": "US",
        "last_message": "We need 40 videos dubbed.", "subject": "[Form] 견적 > 엔터프라이즈 전달",
        "email": "ana@example.test", "ticket_id": "T-1"}
CLASSIFICATION = inbound.ClassifyResult(category="pricing_question", reasoning="")


def _agent(body="Hi Ana,\n\nThanks for writing.\n\nBest regards,", seen=None, **extra):
    seen = seen if seen is not None else {}

    def complete(name, fields, **kwargs):
        if name == "inbound/draft_reply":
            seen.setdefault("calls", []).append((fields.copy(), kwargs))
            return inbound.DraftResult(body=body, language="en", **extra)
        return "번역"

    agent = inbound.InboundAgent.__new__(inbound.InboundAgent)
    agent.llm = MagicMock()
    agent.llm.complete.side_effect = complete
    return agent, seen


# ------------------------------------------------------------------ 회사 지식은 정리된 것이 system 으로


def test_the_draft_reads_every_section_but_the_confidential_ones_and_is_told_its_situation(draft_db):
    """꼬리표로 거르지 않는다 (2026-10-06 평가) — 「첫 회신」으로 달린 판정 규칙이 후속 회신에서 빠졌다."""
    conv_id, doc_id = _conversation(draft_db, organized=True)
    agent, seen = _agent(policy_quotes=[{"source_id": 99, "quote": "Lip-sync is available for all target languages."}])

    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    system = seen["calls"][0][1]["knowledge"]
    assert "이번 회신은 「첫 회신」입니다." in system
    assert "고객이 준 정보는 다시 묻지 않는다." in system and "FIRST_EXAMPLE" in system
    assert "NUDGE_TEMPLATE" in system and "에만 적용)" in system, "다른 회신에 한정된 구간도 「…에만 적용」 표시를 달고 실린다"
    assert "$3.30" not in system, "원가 열은 금지 목록이 지운다"
    assert "PRIVATE_POLICY_SENTINEL" not in system
    trace = draft._context_manifest
    assert trace["knowledge"]["mode"] == "sections" and trace["knowledge"]["map_sha"]
    assert trace["knowledge"]["section_ids"] and all(s.startswith(f"{doc_id}:") for s in trace["knowledge"]["section_ids"])
    # 인용은 원문(정리 전 본문)과 대조된다 — 마크다운 강조가 빠진 인용도, 옆 문서 id 로 단 인용도 진짜 문장이면 통과.
    assert trace["limited_evidence_checks"]["status"] == "PASS"
    # 지문은 정리 전과 같다 — 정리가 끝날 때마다 대기 중인 초안이 「정책이 변경되었습니다」로 막히면 안 된다.
    assert trace["policy"]["sha256"] == PolicySnapshot.capture("first").digest


def test_a_nudge_and_an_answer_are_told_their_situation_and_keep_the_quote_tables(draft_db):
    conv_id, _ = _conversation(draft_db, sales_reply=True, organized=True)
    agent, seen = _agent()
    agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")
    nudge = seen["calls"][-1][1]["knowledge"]
    assert "이번 회신은 「답이 없을 때 다시 보내는 메일」입니다." in nudge
    assert "NUDGE_TEMPLATE" in nudge and "FIRST_EXAMPLE" in nudge

    with draft_db() as session:
        conv = session.get(Conversation, conv_id)
        session.add(CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv_id, channel="이메일",
                                        direction="incoming", summary="What would 480 minutes cost?",
                                        external_id="hubspot:conv:r-2", happened_at=NOW))
        session.commit()
    agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")
    answer = seen["calls"][-1][1]["knowledge"]
    assert "이번 회신은 「고객 답장에 대한 회신」입니다." in answer
    assert "$9.10" in answer, "고객 답장에 답할 때는 견적 계산용 단가가 실린다"
    assert "$3.30" not in answer, "원가 열은 금지 목록이 지운다"


def test_an_organizer_failure_falls_back_to_the_whole_documents_not_to_nothing(draft_db, monkeypatch):
    conv_id, _ = _conversation(draft_db)

    def broken(*args, **kwargs):
        raise PolicyContextError("정리된 정책을 읽지 못했습니다.")

    monkeypatch.setattr(organizer, "knowledge_for", broken)
    agent, seen = _agent()

    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    system = seen["calls"][0][1]["knowledge"]
    assert system == PolicySnapshot.capture("first").rules, "오늘과 같은 문서 통째로"
    assert draft._context_manifest["knowledge"] == {"mode": "full", "map_sha": None, "section_ids": [],
                                                    "error": "PolicyContextError"}


def test_the_client_sends_the_organized_knowledge_as_system_only_with_its_snapshot(draft_db):
    from src.llm.client import LLMClient

    _conversation(draft_db)
    snap = PolicySnapshot.capture("first")
    dispatch = MagicMock(return_value="ok")
    with patch.object(LLMClient, "_dispatch", dispatch):
        LLMClient().complete("test/hello", {"name": "X"}, stage="first", policy_snapshot=snap, knowledge="ORGANIZED")
        assert dispatch.call_args.kwargs["system"] == "ORGANIZED"
        LLMClient().complete("test/hello", {"name": "X"}, stage="first", policy_snapshot=snap)
        assert dispatch.call_args.kwargs["system"] == snap.rules
        with pytest.raises(ValueError):
            LLMClient().complete("test/hello", {"name": "X"}, knowledge="ORGANIZED")


# ------------------------------------------------------------------ 본문 · 검사 · 표시


def test_an_empty_body_is_the_one_draft_failure_that_stays_terminal(draft_db):
    conv_id, _ = _conversation(draft_db)
    agent, _ = _agent(body="   ")
    with pytest.raises(DraftEvidenceError):
        agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")


@pytest.mark.parametrize("repair_fixes_it", [True, False])
def test_a_number_missing_from_the_body_gets_one_repair_then_is_flagged(draft_db, repair_fixes_it, caplog):
    conv_id, _ = _conversation(draft_db)
    feedback = []
    caplog.set_level(logging.INFO)

    def complete(name, fields, **kwargs):
        if name != "inbound/draft_reply":
            return "번역"
        feedback.append(fields["evidence_feedback"])
        body = ("Lip-sync works in 32 languages." if repair_fixes_it and len(feedback) == 2
                else "Lip-sync works in many languages.")
        return inbound.DraftResult(body=body, language="en", answer_points=[
            {"question": "languages?", "supported_answer": "Lip-sync works in 32 languages."}])

    agent = inbound.InboundAgent.__new__(inbound.InboundAgent)
    agent.llm = MagicMock()
    agent.llm.complete.side_effect = complete
    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    checks = draft._context_manifest["limited_evidence_checks"]
    assert checks["generation_attempts"] == 2 and feedback[0] == ""
    assert "answer_point_missing" in feedback[1]
    assert checks["status"] == ("PASS" if repair_fixes_it else "FAIL")
    assert ("answer_point_missing" in checks["issues"]) is not repair_fixes_it
    # 매니페스트에만 남습니다 — 경고 이상은 콘솔 「운영 로그」 탭에 뜨므로 이 표시는 그 아래 수준입니다.
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "근거 검사" in r.getMessage()]


def test_the_body_is_not_rewritten_and_no_warning_is_recorded(draft_db):
    """초안에 경고를 달지 않는다 (2026-10-06 운영자: 「답변 작성에 대한 경고문 이런건 필요없어」). 금액 줄도
    처리 완료 표현도 본문 그대로다. 채우지 않은 자리는 초안이 아니라 승인이 막는다(`approval.approve`)."""
    conv_id, _ = _conversation(draft_db)
    body = ("Hi Ana,\n\nThe Starter plan is $19 per month.\n\nYou can pay here: [[payment link]]\n\n"
            "I have refunded your last payment.\n\nBest regards,")
    agent, _ = _agent(body=body)

    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    assert draft.body == body, "금액 줄도 지우지 않는다"
    assert "flags" not in draft._context_manifest
    dumped = json.dumps(draft._context_manifest, ensure_ascii=False)
    for text in ("$19", "payment link", "refunded", "Ana Customer", "고객이 준 정보"):
        assert text not in dumped, text


# ------------------------------------------------------------------ 제목


def test_a_follow_up_continues_the_newest_email_subject(draft_db):
    conv_id, _ = _conversation(draft_db, sales_reply=True, customer_reply=True)
    agent, _ = _agent(subject="A brand new subject")

    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    assert draft.subject == "RE: Dubbing your training videos"
    assert draft._context_manifest["subject_source"] == "thread"


def test_a_first_reply_never_takes_the_ticket_name(draft_db):
    """접수 행의 제목은 허브스팟 티켓 이름 — CS 가 붙인 「[Form] … > 엔터프라이즈 전달」 같은 내부 이름이다."""
    conv_id, _ = _conversation(draft_db)
    agent, _ = _agent(subject="")

    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    assert draft.subject == "Your inquiry"
    assert draft._context_manifest["subject_source"] == "generic"


def test_a_first_reply_takes_the_customers_form_subject_when_the_model_offers_none(draft_db):
    """폼 제출의 허브스팟 사본은 대화에서 접수 행의 사본으로 접힌다(본문이 같다) — 고객이 쓴 제목은 그 사본에만
    있으므로 제목은 대화가 아니라 표에서 읽는다."""
    conv_id, _ = _conversation(draft_db)
    with draft_db() as session:
        conv = session.get(Conversation, conv_id)
        session.add(CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv_id, channel="폼",
                                        direction="incoming", subject="Video dubbing for our courses",
                                        summary="Service: dubbing Details: We need 40 videos dubbed.",
                                        external_id="hubspot:conv:form-1", happened_at=NOW - timedelta(days=6)))
        session.commit()
    assert all(turn.role != "form" for turn in inbound.thread_events(conv_id)), "사본으로 접혔다"
    agent, _ = _agent(subject="[Form] 40 videos")  # 콘솔 글에 없는 꼬리표 — 떨어진다

    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    assert draft.subject == "Video dubbing for our courses"
    assert draft._context_manifest["subject_source"] == "customer"


def test_an_email_inquiry_continues_its_crm_thread_on_the_first_reply(draft_db):
    """이메일로 들어온 문의는 첫 초안 때 우리 DB 에 아직 스레드가 없다(수집은 몇 분 뒤) — 그 티켓에 붙은
    CRM 메일의 제목이 「RE: <고객이 쓴 제목>」의 출처다."""
    conv_id, _ = _conversation(draft_db)
    agent, _ = _agent(subject="Something else")

    draft = agent._draft_reply({**INFO, "crm_thread_subject": "Question about Spanish dubbing"},
                               CLASSIFICATION, conv_id, "en")

    assert draft.subject == "RE: Question about Spanish dubbing"
    assert draft._context_manifest["subject_source"] == "crm_thread"


@pytest.mark.parametrize("thread,expected", [
    ("Re: RE: Pricing", "RE: Pricing"),
    ("회신: 견적 문의", "RE: 견적 문의"),
    ("Pricing\r\nfor dubbing", "RE: Pricing for dubbing"),  # 발송이 제목의 CR/LF 를 거절한다
])
def test_an_existing_thread_subject_gets_exactly_one_re(thread, expected):
    assert choose_reply_subject(thread_subject=thread, proposed="ignored", target="en") == (expected, "thread")


@pytest.mark.parametrize("subject,target,first_reply,console,ok", [
    ("Dubbing 40 videos into Spanish", "en", True, "", True),
    ("[Perso Dubbing] 40 videos into Spanish", "en", True, "Subject: [Perso Dubbing] …", True),
    ("[Perso Dubbing] 40 videos into Spanish", "en", True, "", False),   # 콘솔 글에 없는 꼬리표
    ("[Chatbot] 문의 접수", "ko", True, "", False),
    ("맞춤형 플랜 문의", "en", True, "", False),                           # 영어 고객에게 한글 제목
    ("맞춤형 플랜 문의", "ko", True, "", True),
    ("Dubbing quote", "ko", True, "", False),                             # 한국어 메일에 한글 없는 제목
    ("お見積りのご案内", "ja", True, "", True),
    ("Seu curso de 130 vídeos: algumas perguntas", "pt", True, "", True),
    ("PRO plan at $99", "en", True, "", False),                           # 첫 회신의 금액
    ("PRO plan at $99", "en", False, "", True),
    ("Write to sales@example.test", "en", False, "", False),
    ("See https://example.test", "en", False, "", False),
    ("Hi {{SENDER_NAME}}", "en", False, "", False),
    ("Hi", "en", False, "", False),
    ("x" * 121, "en", False, "", False),
])
def test_a_proposed_subject_is_checked_not_repaired(subject, target, first_reply, console, ok):
    assert bool(valid_subject(subject, target=target, first_reply=first_reply, console_text=console)) is ok


def test_the_fallbacks_run_in_order():
    assert choose_reply_subject(thread_subject=None, proposed="Fine subject", customer_subject="Form subject",
                                target="en") == ("Fine subject", "model")
    assert choose_reply_subject(thread_subject=" ", proposed="[Form] x", customer_subject="Form subject",
                                target="en") == ("Form subject", "customer")
    assert choose_reply_subject(thread_subject=None, proposed=None, customer_subject=None,
                                target="ko") == ("문의 주신 건", "generic")


# ------------------------------------------------------------------ 대화 맥락 · 지난 메일 참고


def test_crm_email_copies_are_dropped_once_the_thread_is_in_the_conversation(draft_db):
    conv_id, _ = _conversation(draft_db)
    info = {**INFO, "recent_emails": "(참고 — 허브스팟 CRM 메일 기록)\n- [2026-09-01] old: CRM_COPY_SENTINEL"}
    agent, seen = _agent()
    agent._draft_reply(info, CLASSIFICATION, conv_id, "en")
    assert "CRM_COPY_SENTINEL" in seen["calls"][-1][0]["enrichment_context"], "아직 스레드가 없으면 참고로 싣는다"

    with draft_db() as session:
        conv = session.get(Conversation, conv_id)
        session.add(CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv_id, channel="이메일",
                                        direction="outgoing", summary="Forwarded to sales.", handler="support@perso.ai",
                                        external_id="hubspot:conv:cs-1", happened_at=NOW - timedelta(days=6)))
        session.commit()
    agent._draft_reply(info, CLASSIFICATION, conv_id, "en")
    assert "CRM_COPY_SENTINEL" not in seen["calls"][-1][0]["enrichment_context"], "스레드가 들어왔으면 사본은 뺀다"


def test_the_crm_block_is_labelled_and_remembers_the_tickets_email_subject():
    from src.integrations.hubspot_models import EngagementDTO

    hubspot = MagicMock()
    hubspot.get_recent_emails_sync.return_value = [
        EngagementDTO(id="1", type="incoming_email", subject="Other ticket", body="x", ticket_id="T-9",
                      timestamp=datetime(2026, 8, 1)),
        EngagementDTO(id="2", type="incoming_email", subject="Question about Spanish dubbing", body="Hello",
                      ticket_id="T-1", timestamp=datetime(2026, 9, 1)),
    ]
    hubspot.get_associated_deals_sync.return_value = []
    agent = inbound.InboundAgent.__new__(inbound.InboundAgent)
    agent.hubspot = hubspot
    agent.llm = MagicMock()
    info = {"object_id": "c-1", "ticket_id": "T-1", "email": "ana@gmail.com"}

    agent._enrich_draft_context(info)

    assert info["recent_emails"].startswith("(참고 — 허브스팟 CRM 메일 기록")
    assert "- [2026-09-01] Question about Spanish dubbing: Hello" in info["recent_emails"]
    assert info["crm_thread_subject"] == "Question about Spanish dubbing"


def test_precedents_are_offered_after_the_context_and_recorded_by_id(draft_db, monkeypatch):
    conv_id, _ = _conversation(draft_db, sales_reply=True, customer_reply=True)
    asked = {}

    def precedents_for(conv, *, customer_text, situation, llm):
        asked.update(conv=conv, customer_text=customer_text, situation=situation)
        return {"text": "## 우리 팀이 비슷한 상황에서 실제로 보낸 메일 (참고용)\nPRECEDENT_BODY", "ids": ["interaction:7"],
                "candidates": 3}

    monkeypatch.setitem(sys.modules, "src.llm.precedents",
                        types.SimpleNamespace(precedents_for=precedents_for))
    agent, seen = _agent()

    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    assert asked == {"conv": conv_id, "customer_text": "How long does it take?", "situation": "answer_reply"}
    prompt = load_prompt("inbound/draft_reply", seen["calls"][0][0])
    assert prompt.index("이전 대화 맥락") < prompt.index("PRECEDENT_BODY") < prompt.index("엄격한 JSON")
    assert draft._context_manifest["precedents"] == ["interaction:7"]


def _router_down(*args, **kwargs):
    raise RuntimeError("router down")


class _BreaksWhileLoading:
    """가져오는 순간 깨지는 모듈 — ImportError 가 아닌 예외도 초안을 막지 않아야 합니다."""

    def __getattr__(self, name):
        raise RuntimeError("precedents module failed while loading")


@pytest.mark.parametrize("module", [types.SimpleNamespace(precedents_for=_router_down), _BreaksWhileLoading()],
                         ids=["call-raises", "import-raises"])
def test_a_broken_precedent_lookup_never_fails_the_draft(draft_db, monkeypatch, module):
    conv_id, _ = _conversation(draft_db)
    monkeypatch.setitem(sys.modules, "src.llm.precedents", module)
    agent, seen = _agent()

    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    assert seen["calls"][0][0]["precedents"] == ""
    assert draft._context_manifest["precedents"] == []


def test_the_real_precedent_module_reaches_the_draft_prompt_and_only_its_id_the_manifest(draft_db, monkeypatch):
    """위 둘은 가짜 모듈입니다. 여기는 진짜 `llm.precedents` 가 초안 안에서 돕니다 — 이름 · 인자 · 돌려주는 칸이
    어긋나면 울타리가 그 오류를 삼켜 선례가 **조용히** 안 실리므로, 그 어긋남을 여기서 잡습니다."""
    conv_id, _ = _conversation(draft_db)
    with draft_db() as session:
        other = Contact(normalized_email="bo@example.org", email="bo@example.org", full_name="Bo Other")
        session.add(other)
        session.flush()
        conv = Conversation(contact_id=other.id, stage="contacted", hubspot_ticket_id="T-2", inquiry_language="en")
        session.add(conv)
        session.flush()
        session.add(Message(conversation_id=conv.id, direction="inbound", channel="email",
                            body="Can you dub ASK_MARKER into Spanish?", status="received",
                            created_at=NOW - timedelta(days=4)))
        reply = Message(conversation_id=conv.id, direction="outgoing", channel="email",
                        body="Hi Bo,\n\nYes, REPLY_MARKER.\n\nBest regards,", status="sent",
                        created_at=NOW - timedelta(days=3), sent_at=NOW - timedelta(days=3))
        session.add(reply)
        session.commit()
        reply_ref = f"message:{reply.id}"
    monkeypatch.setitem(sys.modules, "src.llm.precedents", precedents)  # 픽스처가 막아 둔 진짜 모듈
    agent, seen = _agent()
    draft_only = agent.llm.complete.side_effect
    routed = {}

    def complete(name, fields, **kwargs):
        if name == "inbound/select_precedents":
            routed.update(fields)
            return precedents.SelectPrecedentsResult(ids=[reply_ref])
        return draft_only(name, fields, **kwargs)

    agent.llm.complete.side_effect = complete

    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    assert routed["situation"] == "first_reply" and routed["customer_text"] == "We need 40 videos dubbed."
    assert reply_ref in routed["index"]
    fields = seen["calls"][0][0]
    assert fields["precedents"].startswith(precedents.HEADER) and "ASK_MARKER" in fields["precedents"]
    prompt = load_prompt("inbound/draft_reply", fields)
    assert prompt.index("이전 대화 맥락") < prompt.index("REPLY_MARKER") < prompt.index("엄격한 JSON")
    assert draft._context_manifest["precedents"] == [reply_ref]
    assert "REPLY_MARKER" not in json.dumps(draft._context_manifest, ensure_ascii=False), "남의 메일 글은 이벤트에 안 남는다"


# ------------------------------------------------------------------ 진짜 파서를 지나는 한 바퀴


_GEMINI_JSON = """```json
{
  "subject": "Dubbing your 40 training videos into Spanish",
  "body": "Hi Ana,\\n\\nThanks for reaching out about dubbing your training videos into Spanish.\\n\\n- 40 videos of about 12 minutes each come to roughly 480 minutes in total.\\n- Lip-sync is available for all target languages.\\n\\nOnce you confirm the scope, you can complete the order here: [[payment link]]\\n\\nBest regards,",
  "answer_points": [
    {"question": "How many minutes is the project?", "supported_answer": "40 videos of about 12 minutes are roughly 480 minutes.", "verification_needed": null},
    {"question": "Is lip-sync available?", "supported_answer": "Lip-sync is available for all target languages.", "verification_needed": null}
  ],
  "policy_quotes": [{"source_id": "SOURCE_ID", "quote": "Lip-sync is available for all target languages."}],
  "placeholders": ["[[payment link]]"],
  "language": "en"
}
```"""


def test_one_draft_through_the_real_client_parser(draft_db, monkeypatch):
    """모델 응답을 가짜 객체로 돌려주는 다른 테스트들은 스키마 검증을 건너뜁니다. 여기는 Gemini 가 실제로
    돌려주는 모양(펜스로 감싼 JSON, 정수 대신 글자인 source_id)이 `LLMClient` 의 파서를 지나 초안이 되고,
    `_finalize_draft` 까지 가는 한 바퀴입니다."""
    from src.llm.client import LLMClient

    conv_id, doc_id = _conversation(draft_db)
    with draft_db() as session:
        message_id = session.query(Message).filter_by(conversation_id=conv_id, direction="inbound").one().id
        draft_row = Message(conversation_id=conv_id, direction="outgoing", channel="email", body="",
                            status="drafting", target_language="en")
        session.add(draft_row)
        session.commit()
        draft_id = draft_row.id
    calls = []

    def call_gemini(prompt, *, max_tokens, system, model, thinking_level, grounded):
        calls.append({"prompt": prompt, "max_tokens": max_tokens, "system": system,
                      "thinking_level": thinking_level})
        if "엄격한 JSON만 반환" in prompt:
            text = _GEMINI_JSON.replace('"SOURCE_ID"', f'"{doc_id}"')
        else:
            text = "안녕하세요 아나님, 스페인어 더빙 문의 감사합니다."
        return LLMResult(text=text, input_tokens=10, output_tokens=5, model="gemini-test")

    monkeypatch.setattr("src.llm.client.call_gemini", call_gemini)
    monkeypatch.setattr(LLMClient, "_log_event", lambda *a, **k: None)
    agent = inbound.InboundAgent.__new__(inbound.InboundAgent)
    agent.llm = LLMClient()

    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")
    assert agent._finalize_draft(draft_id, INFO, CLASSIFICATION, draft, conv_id, "en")

    expected_body = json.loads(_GEMINI_JSON.strip("`").removeprefix("json"))["body"]
    assert draft.body == expected_body, "모델이 쓴 본문 그대로 — 코드는 고쳐 쓰지 않는다"
    assert draft.subject == "Dubbing your 40 training videos into Spanish"
    assert [q.source_id for q in draft.policy_quotes] == [doc_id] and draft.placeholders == ["[[payment link]]"]
    assert draft.body_ko == "안녕하세요 아나님, 스페인어 더빙 문의 감사합니다."
    draft_call = next(call for call in calls if "엄격한 JSON만 반환" in call["prompt"])
    assert draft_call["max_tokens"] == 8000 and draft_call["thinking_level"] == "LOW"
    assert "Lip-sync" in draft_call["system"] and "PRIVATE_POLICY_SENTINEL" not in draft_call["system"]
    assert "We need 40 videos dubbed." in draft_call["prompt"]
    with draft_db() as session:
        stored = session.get(Message, draft_id)
        assert (stored.status, stored.subject, stored.body) == ("pending_approval", draft.subject, expected_body)
        trace = session.query(Event).filter_by(kind="reply_context").one().payload
    assert trace["message_id"] == draft_id and message_id
    assert trace["situation"] == "first_reply" and trace["subject_source"] == "model"
    assert trace["knowledge"]["mode"] == "full"
    assert trace["limited_evidence_checks"]["status"] == "PASS"
    assert trace["limited_evidence_checks"]["quoted_source_ids"] == [doc_id]
    assert trace["limited_evidence_checks"]["answer_points_count"] == 2
    assert "flags" not in trace
    # 예전 매니페스트의 칸은 전부 그대로 — 승인 · 발송 관문이 읽는다(`reply_safety`).
    for key in ("policy", "selection", "generated_at", "turn_refs", "thread_sha256", "input_sha256", "body_sha256",
                "prompt_sha256", "schema_sha256", "semantic_validation", "delivery_permission", "precedents"):
        assert key in trace, key
    dumped = json.dumps(trace, ensure_ascii=False)
    assert "Ana" not in dumped and "Lip-sync" not in dumped


# ------------------------------------------------------------------ 보내기 전 검토


def _reviewing_agent(problems, *, fail=False):
    seen = {"draft": [], "review": []}
    bodies = iter(("FIRST DRAFT body.", "SECOND DRAFT body."))

    def complete(name, fields, **kwargs):
        if name == "inbound/draft_reply":
            seen["draft"].append((fields.copy(), kwargs))
            return inbound.DraftResult(body=next(bodies), language="en")
        if name == "inbound/review_draft":
            seen["review"].append((fields.copy(), kwargs))
            if fail:
                raise RuntimeError("model down")
            return problems
        return "번역"

    agent = inbound.InboundAgent.__new__(inbound.InboundAgent)
    agent.llm = MagicMock()
    agent.llm.complete.side_effect = complete
    return agent, seen


def test_a_problem_the_review_finds_gets_one_rewrite_that_sees_the_previous_draft(draft_db):
    """검토의 기준은 초안이 읽은 그 문서다 — 규칙을 코드에 옮기지 않는다 (2026-10-06)."""
    conv_id, _ = _conversation(draft_db, organized=True)
    review = inbound.DraftReview(problems=[
        inbound.ReviewProblem(quote="FIRST DRAFT", problem="문서 §2 와 어긋남", fix="문서대로 고친다"),
        inbound.ReviewProblem(quote="", problem="  ", fix="빈 문제는 세지 않는다"),
    ])
    agent, seen = _reviewing_agent(review)

    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    # 검토 한 번, 재작성 한 번 — 고친 글을 다시 검토하지 않는다(비용과 지연의 상한).
    assert len(seen["review"]) == 1 and len(seen["draft"]) == 2
    review_fields, review_kwargs = seen["review"][0]
    assert review_fields["draft_body"] == "FIRST DRAFT body."
    assert review_kwargs["knowledge"] == seen["draft"][0][1]["knowledge"]
    assert review_kwargs["policy_snapshot"] is seen["draft"][0][1]["policy_snapshot"]
    feedback = seen["draft"][1][0]["evidence_feedback"]
    assert "문서 §2 와 어긋남" in feedback and "문서대로 고친다" in feedback
    assert "FIRST DRAFT body." in feedback, "직전 초안을 같이 줘야 지적된 곳만 고친다"
    assert draft.body.startswith("SECOND DRAFT")
    assert draft._context_manifest["review_problems"] == 1


def test_a_clean_review_keeps_the_first_draft(draft_db):
    conv_id, _ = _conversation(draft_db, organized=True)
    agent, seen = _reviewing_agent(inbound.DraftReview())

    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    assert len(seen["review"]) == 1 and len(seen["draft"]) == 1
    assert draft.body.startswith("FIRST DRAFT")
    assert draft._context_manifest["review_problems"] == 0


def test_a_failed_review_does_not_fail_the_draft(draft_db):
    conv_id, _ = _conversation(draft_db, organized=True)
    agent, seen = _reviewing_agent(inbound.DraftReview(), fail=True)

    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    assert len(seen["review"]) == 1 and len(seen["draft"]) == 1
    assert draft.body.startswith("FIRST DRAFT")


@pytest.mark.parametrize(("sales_reply", "ordinal"), [(False, "1번째"), (True, "2번째")])
def test_the_situation_says_which_of_our_emails_this_is(draft_db, sales_reply, ordinal):
    """문서는 회신을 차수로 센다(「1차 회신에서는 …」) — 몇 번째 메일인지가 있어야 첫 회신에만 걸린 조건을 가린다."""
    conv_id, _ = _conversation(draft_db, sales_reply=sales_reply, customer_reply=sales_reply)
    agent, seen = _agent()

    agent._draft_reply(INFO, CLASSIFICATION, conv_id, "en")

    assert f"이번 메일은 우리 영업이 이 대화에서 보내는 {ordinal} 이메일입니다." in seen["calls"][0][0]["situation"]
