"""Stable formatting and tone requirements in reply prompts."""

from __future__ import annotations

from src.llm.prompts import get_company_rules, load_prompt


def test_draft_prompt_requires_scannable_plain_text_layout() -> None:
    prompt = load_prompt(
        "inbound/draft_reply",
        {
            "contact_name": "고객",
            "company": "Example",
            "country": "KR",
            "category": "support",
            "score": "70",
            "last_message": "기능 두 가지를 문의합니다.",
            "conversation_context": "이전 문의가 있습니다.",
            "enrichment_context": "",
            "knowledge_docs": "",
            "pricing_rule": "확인된 정책만 사용합니다.",
        },
        include_rules=False,
    )

    assert "한 줄에는 한 문장 또는 한 가지 요점만" in prompt
    assert "각 줄을 정확히 `- `로 시작" in prompt
    assert "다음 행동은 회신, 미팅, 결제, 자료 전달 중" in prompt
    assert "이전 대화 맥락" in prompt
    assert "이전 문의가 있습니다." in prompt


def test_company_rules_allow_polite_requests_and_one_cta() -> None:
    get_company_rules.cache_clear()
    rules = get_company_rules()

    assert "말씀해 주세요" in rules
    assert "메일 하나의 CTA는 하나만" in rules
    assert "선호 채널" in rules


def test_translation_prompt_preserves_dash_bullets() -> None:
    prompt = load_prompt(
        "util/translate_to",
        {"target_language": "English", "text": "안내\n\n- 조건 1\n- 조건 2"},
        include_rules=False,
    )

    assert "Keep every `- ` bullet" in prompt
    assert "paragraph breaks" in prompt
