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

_ROLE_PREFIXES: frozenset[str] = frozenset({
    "info",
    "sales",
    "hello",
    "contact",
    "admin",
    "support",
    "hr",
    "recruit",
    "noreply",
    "no-reply",
    "marketing",
    "webmaster",
    "postmaster",
    "abuse",
})


def is_personal_domain(domain: str) -> bool:
    """Return True if the domain belongs to a free/personal email provider."""
    return domain.lower().strip() in PERSONAL_DOMAINS


def is_role_address(local_part: str) -> bool:
    """Return True if the local part is a generic role address (info@, sales@, etc.). Used by inbound contact scoring."""
    return local_part.lower().strip() in _ROLE_PREFIXES
