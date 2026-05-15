"""Country-to-language mapping for outbound email drafting."""

from __future__ import annotations

COUNTRY_LANGUAGE_MAP: dict[str, str] = {
    "KR": "ko",
    "JP": "ja",
    "US": "en",
    "GB": "en",
    "AU": "en",
    "CA": "en",
    "NZ": "en",
    "IE": "en",
    "DE": "de",
    "AT": "de",
    "CH": "de",
    "FR": "fr",
    "ES": "es",
    "MX": "es",
    "AR": "es",
    "CL": "es",
    "CO": "es",
    "PE": "es",
    "BR": "pt",
    "PT": "pt",
    "CN": "zh",
    "TW": "en",
    "HK": "en",
    "SG": "en",
    "MY": "en",
    "ID": "en",
    "VN": "en",
    "TH": "en",
    "PH": "en",
    "IN": "en",
    "IL": "en",
    "AE": "en",
    "SA": "en",
    "SE": "en",
    "NO": "en",
    "DK": "en",
    "FI": "en",
    "NL": "en",
    "BE": "en",
    "IT": "it",
    "RU": "ru",
    "TR": "tr",
    "PL": "pl",
}

DEFAULT_LANGUAGE = "en"


def guess_language(country: str | None, llm_guess: str | None = None) -> str:
    """Determine email language from LLM guess and country code."""
    if llm_guess and len(llm_guess) == 2:
        return llm_guess.lower()

    if country:
        return COUNTRY_LANGUAGE_MAP.get(country.upper(), DEFAULT_LANGUAGE)

    return DEFAULT_LANGUAGE
