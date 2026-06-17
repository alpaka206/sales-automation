"""The inbound reply must go out in the inquiry's language, not the model's default."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.agents.inbound import DraftResult, InboundAgent


def _agent_with_draft(body: str, subject: str, lang: str) -> InboundAgent:
    agent = InboundAgent.__new__(InboundAgent)
    agent.llm = MagicMock()
    agent.llm.complete.return_value = DraftResult(subject=subject, body=body, language=lang)
    return agent


_CI = {
    "last_message": "Hello, can your plans dub 90-minute videos from English to Spanish?",
    "full_name": "Ina",
    "company": "Acme",
    "country": "US",
    "email": "ina@example.com",
    "ticket_id": "T1",
}


@patch("src.agents.inbound.select_relevant_docs", return_value="")
@patch("src.llm.translate.translate_to")
@patch("src.llm.language.detect_language")
def test_korean_draft_for_english_inquiry_is_translated(mock_detect, mock_translate, _docs):
    # Inquiry detected English; the model drafted in Korean → must be translated back.
    mock_detect.side_effect = ["en", "ko"]  # inquiry, then draft body
    mock_translate.side_effect = lambda text, code, **kw: f"[EN]{text}"

    agent = _agent_with_draft(body="한국어 본문입니다", subject="안녕하세요", lang="ko")
    cls = MagicMock()
    cls.category = "pricing_question"

    draft = agent._draft_reply(_CI, cls, 80)

    assert draft.language == "en"
    assert draft.body == "[EN]한국어 본문입니다"
    assert draft.subject == "[EN]안녕하세요"
    assert mock_translate.call_count == 2  # body + subject


@patch("src.agents.inbound.select_relevant_docs", return_value="")
@patch("src.llm.translate.translate_to")
@patch("src.llm.language.detect_language")
def test_matching_language_is_not_translated(mock_detect, mock_translate, _docs):
    # Inquiry English, draft English → no translation call.
    mock_detect.side_effect = ["en", "en"]

    agent = _agent_with_draft(body="Hello, here are our plans.", subject="Re: Inquiry", lang="en")
    cls = MagicMock()
    cls.category = "pricing_question"

    draft = agent._draft_reply(_CI, cls, 80)

    assert draft.language == "en"
    assert draft.body == "Hello, here are our plans."
    mock_translate.assert_not_called()
