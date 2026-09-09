"""이메일 정규화와, 초안 프롬프트에 실을 보강 정보 블록.

**리드 점수는 2026-09-09 에 지웠습니다** (운영자 지시: 「리드 점수가 안 중요했던 것
같아서」). 그 값은 사람이 보는 자리가 하나도 없었습니다 — `contacts.score` 는 쓰기만
하고 읽는 코드가 0, `messages.score_snapshot` 은 API 가 내려보내는데 그리는 화면이
없었습니다. 유일한 소비처가 초안 프롬프트의 「점수: 80」 한 줄이었고, 그 한 줄을 위해
문의마다 Gemini 왕복이 하나 더 났습니다(512Mi 인스턴스에서 그 왕복 하나가 공짜가
아닙니다). 되살릴 거면 **어느 화면이 그 숫자를 읽는지부터 정하세요** — 그게 없어서
이렇게 됐습니다.
"""

from __future__ import annotations

import re

def _normalize_email(email: str) -> str:
    local, _, domain = email.lower().partition("@")
    local = re.sub(r"\+.*$", "", local)
    return f"{local}@{domain}"


def _domain_from_email(email: str) -> str:
    return email.lower().split("@")[-1]


def _build_enrichment_context(contact_info: dict) -> str:
    """Build optional context block from HubSpot-enriched data."""
    parts: list[str] = []
    if contact_info.get("recent_emails"):
        parts.append(f"Recent email history with this contact:\n{contact_info['recent_emails']}")
    if contact_info.get("deal_summary"):
        parts.append(f"Associated deals:\n{contact_info['deal_summary']}")

    dp = contact_info.get("domain_profile")
    if dp:
        lines = [
            "Sender's domain profile (auto-analyzed):",
            f"- domain: {dp.get('domain', '')}",
            f"- inferred company: {dp.get('company_name', 'unknown')} (confidence: {dp.get('confidence', 'low')})",
            f"- industry: {dp.get('industry', 'unknown')}",
            f"- services: {dp.get('services', 'unknown')}",
            f"- target market: {dp.get('target_market', 'unknown')}",
            f"- size hint: {dp.get('size_hint', 'unknown')}",
        ]
        if dp.get("notes"):
            lines.append(f"- notes: {dp['notes']}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)
