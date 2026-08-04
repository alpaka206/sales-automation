"""Stable formatting and tone requirements in reply prompts."""

from __future__ import annotations

from src.llm.prompts import load_prompt


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
    """Asserted against the SEEDED rule text, not against a live database.

    The rules are rows in ``policy_sources`` now (edited in Notion, synced), so
    get_company_rules() is empty without a database — but the text this repository ships
    as the starting point still has to say these things, or a fresh install starts with
    rules that contradict the reviewed ones.
    """
    import pathlib

    seed = pathlib.Path("src/db/seeds/policy/rule_01_tone.md").read_text(encoding="utf-8")

    assert "말씀해 주세요" in seed
    assert "메일 하나의 CTA는 하나만" in seed
    assert "선호 채널" in seed


def test_translation_prompt_preserves_dash_bullets() -> None:
    prompt = load_prompt(
        "util/translate_to",
        {"target_language": "English", "text": "안내\n\n- 조건 1\n- 조건 2"},
        include_rules=False,
    )

    assert "Keep every `- ` bullet" in prompt
    assert "paragraph breaks" in prompt


# ---- The reply SHAPE is a DB row, not a prompt file ------------------------------


def test_reply_format_reaches_the_draft_prompt():
    """Editing 답변 메일 형식 in the console must change the next draft, not the next deploy."""
    from unittest.mock import patch

    from src.llm.prompts import get_reply_format, load_prompt

    assert "{{reply_format}}" in load_prompt("inbound/draft_reply", include_rules=False)

    with patch("src.db.email_templates.get_email_template", return_value="  뼈대  "):
        assert get_reply_format() == "뼈대"


def test_a_template_outage_does_not_block_drafting():
    from unittest.mock import patch

    from src.llm.prompts import get_reply_format

    with patch("src.db.email_templates.get_email_template", side_effect=RuntimeError("db down")):
        assert get_reply_format() == ""


def test_links_are_substituted_not_generated():
    """The booking URL is ~120 chars of opaque base64 — a model would tidy it."""
    from unittest.mock import patch

    from src.db.migrations import __name__ as _  # noqa: F401  (keeps import ordering honest)
    from src.llm.prompts import apply_link_tokens

    values = {"meeting_link": "https://calendar.example/abc123", "whatsapp_link": "https://wa.me/1"}
    with patch("src.db.email_templates.get_email_template", side_effect=values.get):
        out = apply_link_tokens("미팅 예약: {{MEETING_LINK}}\nWhatsApp: {{WHATSAPP}}")
    assert out == "미팅 예약: https://calendar.example/abc123\nWhatsApp: https://wa.me/1"


def test_an_unset_link_stays_visible_instead_of_vanishing():
    """A blank would ship a sentence promising a link that is not there."""
    from unittest.mock import patch

    from src.llm.prompts import apply_link_tokens

    with patch("src.db.email_templates.get_email_template", return_value=""):
        assert apply_link_tokens("예약: {{MEETING_LINK}}") == "예약: {{MEETING_LINK}}"


def test_seeded_format_names_exactly_the_tokens_the_code_substitutes():
    """A token in the skeleton that the code does not know ships to the customer raw."""
    import importlib

    from src.llm.prompts import _LINK_TOKENS

    seed = importlib.import_module("src.db.migrations.0042_reply_format_template")
    for token in _LINK_TOKENS:
        assert token in seed.REPLY_FORMAT, token
    assert seed.WHATSAPP_LINK.endswith("821054802261")
