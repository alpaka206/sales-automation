"""Reply rules are enforced in CODE, not the prompt.

- the draft is in the INQUIRY's language (translated if the model wrote Korean),
  and the Korean the operator reviews against is stored beside it;
- the subject is "RE: <customer subject>" with exactly one RE:;
- the first reply never states a price (offending lines are stripped).

What actually goes OUT in the inquiry's language is covered by the send-guard
tests in test_rule_guards.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.agents.inbound import DraftResult, InboundAgent


def _agent_with_draft(body: str, subject: str = "ignored", lang: str = "en") -> InboundAgent:
    agent = InboundAgent.__new__(InboundAgent)
    agent.llm = MagicMock()

    def side_effect(prompt_name, variables=None, schema=None, **kw):
        if "draft_reply" in prompt_name:
            return DraftResult(subject=subject, body=body, language=lang)
        if "translate_ko" in prompt_name:
            return "한국어로 번역된 본문"
        return "ok"

    agent.llm.complete = MagicMock(side_effect=side_effect)
    return agent


_CI = {
    "last_message": "Hello, can your plans dub 90-minute videos from English to Spanish?",
    "full_name": "Ina",
    "company": "Acme",
    "country": "US",
    "email": "ina@example.com",
    "subject": "Pricing for dubbing",
    "inquiry_language": "en",
}


@patch("src.agents.inbound.select_relevant_docs", return_value=("", None))
def test_an_english_draft_stays_english_and_keeps_a_korean_reading(_docs):
    """영문 문의의 초안은 영어 그대로 남고, 한국어는 옆 칸에 **저장**됩니다.

    예전에는 이 자리에서 한국어로 번역했습니다. 그러면 정책 문서에 운영자가 영어로 써 둔
    완성된 메일이 고객에게 그대로 갈 길이 없습니다 — 모델이 한국어로 다시 쓰고, 승인 때
    번역기가 그 한국어를 영어로 되돌립니다(2026-08-26, msg 64).
    """
    agent = _agent_with_draft(body="Hello, here are our plans.")
    cls = MagicMock()
    cls.category = "pricing_question"

    draft = agent._draft_reply(_CI, cls, 80, conv_id=None, inquiry_lang="en")

    assert draft.language == "en"
    assert draft.body == "Hello, here are our plans."
    # 대역은 초안 때 한 번 만들어집니다 — 화면이 열릴 때마다가 아니라.
    assert draft.body_ko == "한국어로 번역된 본문"


@patch("src.agents.inbound.select_relevant_docs", return_value=("", None))
def test_a_korean_draft_for_a_korean_inquiry_has_no_second_copy(_docs):
    """한국어 문의는 본문이 곧 한국어라 옆 칸이 비어 있습니다 — 같은 글을 두 번 두지 않습니다."""
    agent = _agent_with_draft(body="안녕하세요, 플랜 안내드립니다.", lang="ko")
    cls = MagicMock()
    cls.category = "pricing_question"

    draft = agent._draft_reply({**_CI, "inquiry_language": "ko"}, cls, 80, conv_id=None,
                               inquiry_lang="ko")

    assert draft.language == "ko"
    assert draft.body_ko == ""


@patch("src.agents.inbound.select_relevant_docs", return_value=("", None))
def test_a_model_that_wrote_korean_anyway_is_moved_to_the_send_language(_docs):
    """프롬프트도 참고 문서도 한국어라, 모델이 한국어로 써 버리는 것이 이 자리의 실수입니다."""
    agent = _agent_with_draft(body="안녕하세요, 플랜 안내드립니다.", lang="ko")
    agent.llm.complete = MagicMock(side_effect=lambda name, variables=None, schema=None, **kw: (
        DraftResult(subject="s", body="안녕하세요, 플랜 안내드립니다.", language="ko")
        if "draft_reply" in name
        else ("Hello, here is the plan." if "translate_to" in name else "한국어 대역")
    ))
    cls = MagicMock()
    cls.category = "pricing_question"

    draft = agent._draft_reply(_CI, cls, 80, conv_id=None, inquiry_lang="en")

    assert draft.language == "en"
    assert draft.body == "Hello, here is the plan."


@patch("src.agents.inbound.select_relevant_docs", return_value=("", None))
def test_subject_is_re_customer_subject(_docs):
    agent = _agent_with_draft(body="안녕하세요, 플랜 안내드립니다.", lang="ko")
    cls = MagicMock()
    cls.category = "pricing_question"

    draft = agent._draft_reply(_CI, cls, 80, conv_id=None)

    assert draft.subject == "RE: Pricing for dubbing"


@patch("src.agents.inbound.select_relevant_docs", return_value=("", None))
def test_subject_does_not_stack_re(_docs):
    ci = dict(_CI, subject="Re: Pricing for dubbing")
    agent = _agent_with_draft(body="안녕하세요.", lang="ko")
    cls = MagicMock()
    cls.category = "pricing_question"

    draft = agent._draft_reply(ci, cls, 80, conv_id=None)

    assert draft.subject == "RE: Pricing for dubbing"


@patch("src.agents.inbound.select_relevant_docs", return_value=("", None))
def test_first_reply_strips_prices(_docs):
    body = "플랜을 안내드립니다.\n- Creator 플랜 $29/월\n미팅에서 자세히 안내드릴게요."
    agent = _agent_with_draft(body=body, lang="ko")
    cls = MagicMock()
    cls.category = "pricing_question"

    # conv_id=None → treated as the first reply → price line removed in code.
    draft = agent._draft_reply(_CI, cls, 80, conv_id=None)

    assert "$29" not in draft.body
    assert "미팅" in draft.body
