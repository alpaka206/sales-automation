"""Reply rules are enforced in CODE, not the prompt.

- the operator-facing draft is always Korean (translated if the model didn't);
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


@patch("src.agents.inbound.select_relevant_docs", return_value="")
def test_draft_is_forced_to_korean(_docs):
    # Model drafted in English → ensure_korean translates it; stored language = 'ko'.
    agent = _agent_with_draft(body="Hello, here are our plans.")
    cls = MagicMock()
    cls.category = "pricing_question"

    draft = agent._draft_reply(_CI, cls, 80, conv_id=None)

    assert draft.language == "ko"
    assert draft.body == "한국어로 번역된 본문"


@patch("src.agents.inbound.select_relevant_docs", return_value="")
def test_subject_is_re_customer_subject(_docs):
    agent = _agent_with_draft(body="안녕하세요, 플랜 안내드립니다.", lang="ko")
    cls = MagicMock()
    cls.category = "pricing_question"

    draft = agent._draft_reply(_CI, cls, 80, conv_id=None)

    assert draft.subject == "RE: Pricing for dubbing"


@patch("src.agents.inbound.select_relevant_docs", return_value="")
def test_subject_does_not_stack_re(_docs):
    ci = dict(_CI, subject="Re: Pricing for dubbing")
    agent = _agent_with_draft(body="안녕하세요.", lang="ko")
    cls = MagicMock()
    cls.category = "pricing_question"

    draft = agent._draft_reply(ci, cls, 80, conv_id=None)

    assert draft.subject == "RE: Pricing for dubbing"


@patch("src.agents.inbound.select_relevant_docs", return_value="")
def test_first_reply_strips_prices(_docs):
    body = "플랜을 안내드립니다.\n- Creator 플랜 $29/월\n미팅에서 자세히 안내드릴게요."
    agent = _agent_with_draft(body=body, lang="ko")
    cls = MagicMock()
    cls.category = "pricing_question"

    # conv_id=None → treated as the first reply → price line removed in code.
    draft = agent._draft_reply(_CI, cls, 80, conv_id=None)

    assert "$29" not in draft.body
    assert "미팅" in draft.body
