"""Reply rules are enforced in CODE, not the prompt.

- the draft is in the INQUIRY's language (translated if the model wrote Korean),
  and the Korean the operator reviews against is stored beside it;
- the subject is chosen by ``choose_reply_subject`` — never the HubSpot ticket name, never
  a stacked RE: (2026-10-06);
- a price in the first reply stays as written — not stripped, not flagged (2026-10-06).

What actually goes OUT in the inquiry's language is covered by the send-guard
tests in test_rule_guards.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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

    draft = agent._draft_reply(_CI, cls, conv_id=None, inquiry_lang="en")

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

    draft = agent._draft_reply({**_CI, "inquiry_language": "ko"}, cls, conv_id=None,
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

    draft = agent._draft_reply(_CI, cls, conv_id=None, inquiry_lang="en")

    assert draft.language == "en"
    assert draft.body == "Hello, here is the plan."


@patch("src.agents.inbound.select_relevant_docs", return_value=("", None))
def test_a_new_thread_takes_the_checked_model_subject_not_the_ticket_name(_docs):
    """티켓 이름(「Pricing for dubbing」)은 CS 가 붙인 내부 이름일 수 있어 후보가 아닙니다. 이어지는 이메일
    스레드가 없는 첫 회신은 새 스레드라 RE: 없이 — 검사를 지난 모델의 제안입니다."""
    agent = _agent_with_draft(body="Hello, here are our plans.", subject="Dubbing 90-minute videos into Spanish")
    cls = MagicMock()
    cls.category = "pricing_question"

    draft = agent._draft_reply(_CI, cls, conv_id=None, inquiry_lang="en")

    assert draft.subject == "Dubbing 90-minute videos into Spanish"
    assert draft._context_manifest["subject_source"] == "model"


@patch("src.agents.inbound.select_relevant_docs", return_value=("", None))
def test_subject_does_not_stack_re(_docs):
    agent = _agent_with_draft(body="Hello.", subject="Re: RE: Pricing for dubbing")
    cls = MagicMock()
    cls.category = "pricing_question"

    draft = agent._draft_reply(_CI, cls, conv_id=None, inquiry_lang="en")

    assert draft.subject == "Pricing for dubbing"


@pytest.mark.parametrize("proposed", [
    "[Form] 견적 요청",            # 내부 꼬리표 + 한국어 — 힌디어 고객에게 나간 msg 72 의 모양
    "맞춤형 플랜 문의",             # 영어 고객에게 한글 제목(msg 110)
    "Quote: $1.23 per minute",    # 첫 회신의 금액
    "Write to sales@perso.ai",    # 주소
    "Hi",                         # 너무 짧다
])
@patch("src.agents.inbound.select_relevant_docs", return_value=("", None))
def test_a_model_subject_that_fails_the_checks_falls_back_to_the_generic_one(_docs, proposed):
    agent = _agent_with_draft(body="Hello.", subject=proposed)
    cls = MagicMock()
    cls.category = "pricing_question"

    draft = agent._draft_reply(_CI, cls, conv_id=None, inquiry_lang="en")

    assert draft.subject == "Your inquiry"
    assert draft._context_manifest["subject_source"] == "generic"


@patch("src.agents.inbound.select_relevant_docs", return_value=("", None))
def test_a_first_reply_price_stays_as_written_and_unflagged(_docs):
    """첫 회신의 금액은 지우지도 짚지도 않습니다 (2026-10-06). 지우던 동안 줄을 맞춘 항목의 한 줄만 빠지고
    (MSG#110), 콘솔 문서가 첫 회신에 써도 된다고 한 공개 가격까지 사라져 문장이 끊긴 채 나갔습니다(MSG#118).
    경고로 짚는 것도 운영자가 원하지 않았습니다 — 「답변 작성에 대한 경고문 이런건 필요없어」."""
    body = "플랜을 안내드립니다.\n- Creator 플랜 $29/월\n미팅에서 자세히 안내드릴게요."
    agent = _agent_with_draft(body=body, lang="ko")
    cls = MagicMock()
    cls.category = "pricing_question"

    # conv_id=None → first reply.
    draft = agent._draft_reply(_CI, cls, conv_id=None, inquiry_lang="ko")

    assert "$29" in draft.body and "미팅" in draft.body
    assert "flags" not in draft._context_manifest
    assert "$29" not in str(draft._context_manifest)


def test_a_one_word_inquiry_is_measured_with_the_customers_own_ticket_name():
    """본문이 「video」 한 낱말이면 영어로 잡혀 브라질 고객에게 회신도 리마인더도 영어로 갑니다(2026-10-07, R429) —
    고객이 쓴 티켓 이름을 같이 잽니다. CS 가 붙인 내부 이름은 고객의 말이 아니라 안 씁니다."""
    from src.agents.inbound import _language_sample

    assert _language_sample({"subject": "estudo", "last_message": "video"}) == "estudo\nvideo"
    assert _language_sample({"subject": "[Form] 김 > 엔터프라이즈 전달", "last_message": "video"}) == "video"
    assert _language_sample({"subject": "더빙 문의", "last_message": "video"}) == "video"
    long_body = "We need dubbing for forty training videos"
    assert _language_sample({"subject": "estudo", "last_message": long_body}) == long_body
