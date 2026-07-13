"""Tests for multilingual email prompt enforcement and language mapping."""

from __future__ import annotations


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


