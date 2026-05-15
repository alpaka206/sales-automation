"""Tests for multilingual email prompt enforcement and language mapping."""

from __future__ import annotations

from pathlib import Path

from src.common.language import guess_language


def test_guess_language_from_llm() -> None:
    assert guess_language("US", "en") == "en"
    assert guess_language("KR", "ko") == "ko"
    assert guess_language("JP", "ja") == "ja"
    assert guess_language("BR", "pt") == "pt"
    assert guess_language("ES", "es") == "es"


def test_guess_language_llm_overrides_country() -> None:
    assert guess_language("US", "ko") == "ko"
    assert guess_language("KR", "en") == "en"


def test_guess_language_country_fallback() -> None:
    assert guess_language("KR", None) == "ko"
    assert guess_language("JP", None) == "ja"
    assert guess_language("US", None) == "en"
    assert guess_language("GB", None) == "en"
    assert guess_language("DE", None) == "de"
    assert guess_language("FR", None) == "fr"
    assert guess_language("ES", None) == "es"
    assert guess_language("MX", None) == "es"
    assert guess_language("BR", None) == "pt"
    assert guess_language("SG", None) == "en"
    assert guess_language("TW", None) == "en"


def test_guess_language_unknown_country() -> None:
    assert guess_language("ZZ", None) == "en"
    assert guess_language("XX", None) == "en"


def test_guess_language_no_info() -> None:
    assert guess_language(None, None) == "en"


def test_guess_language_empty_string_llm() -> None:
    assert guess_language("KR", "") == "ko"


def test_guess_language_invalid_llm_code() -> None:
    assert guess_language("KR", "korean") == "ko"
    assert guess_language(None, "english") == "en"


PROMPTS_DIR = Path(__file__).parent.parent / "src" / "llm" / "prompts" / "outbound"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def test_all_email_prompts_have_language_enforcement() -> None:
    email_prompts = list(PROMPTS_DIR.glob("email_*.md"))
    assert len(email_prompts) >= 5

    for prompt_path in email_prompts:
        content = prompt_path.read_text(encoding="utf-8")
        assert "Language enforcement" in content, f"{prompt_path.name} missing language enforcement section"
        assert "MUST write the entire email in" in content, f"{prompt_path.name} missing MUST clause"
        assert "Subject + body + signature" in content, f"{prompt_path.name} missing signature clause"


def test_followup_prompt_has_language_enforcement() -> None:
    content = _load_prompt("followup.md")
    assert "Language enforcement" in content
    assert "MUST write the entire email in" in content


def test_icp_score_prompt_has_iso_codes() -> None:
    content = _load_prompt("icp_score.md")
    assert "ISO 639-1" in content
    assert "ja" in content
    assert "es" in content
    assert "pt" in content


def test_prompts_use_flexible_language_output() -> None:
    email_prompts = list(PROMPTS_DIR.glob("email_*.md"))
    for prompt_path in email_prompts:
        content = prompt_path.read_text(encoding="utf-8")
        assert '"ko" | "en"' not in content, (
            f"{prompt_path.name} still has hardcoded ko/en language output"
        )
