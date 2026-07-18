"""Personal/role email domain and address utilities."""

from __future__ import annotations

PERSONAL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com",
    "naver.com",
    "daum.net",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "icloud.com",
    "kakao.com",
    "hanmail.net",
    "nate.com",
    "gmx.com",
    "gmx.net",
    "proton.me",
    "protonmail.com",
    "yandex.com",
    "qq.com",
    "163.com",
})

def is_personal_domain(domain: str) -> bool:
    """Return True if the domain belongs to a free/personal email provider."""
    return domain.lower().strip() in PERSONAL_DOMAINS
