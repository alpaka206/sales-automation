"""Typed DTOs for HubSpot CRM objects (contacts, engagements, deals, tickets)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ContactDTO(BaseModel):
    """A HubSpot contact, flattened to the fields the agents actually use."""

    id: str
    email: str | None = None
    firstname: str | None = None
    lastname: str | None = None
    company: str | None = None
    phone: str | None = None
    country: str | None = None
    # HubSpot's IP-derived country, in English. The form-entered `country` above is
    # usually blank, so this is what the workbook's IP Country column is filled from.
    ip_country: str | None = None
    lifecyclestage: str | None = None
    # 고객 상태 카드가 읽는 셋. 이 셋만 있는 이유는 나머지 다섯(리드 온도·다음 액션·
    # MQL/PQL·유입 소스·파이프라인)이 이 포털의 연락처에 대응 속성이 없거나 워크북 쪽이
    # 수식 칸이기 때문이다 — 2026-08-26 에 549개 속성을 훑어 확인했다.
    plan: str | None = None
    user_seq: str | None = None
    industry: str | None = None


class EngagementDTO(BaseModel):
    """A timeline engagement (email, note, etc.) on a contact."""

    id: str
    type: str
    subject: str | None = None
    body: str | None = None
    timestamp: datetime | None = None
    # 이 메일이 붙어 있는 티켓. **허브스팟이 알려 주는 사실**이라 짐작이 아닙니다 —
    # 한동안 시각으로 「그때 열려 있던 티켓」을 골랐는데, New 티켓에 몇 달 전 메일이
    # 붙었습니다. 없으면 None: 티켓 없이 연락처에만 달린 메일도 있습니다.
    ticket_id: str | None = None


class DealDTO(BaseModel):
    """A deal associated with a contact — used to give the LLM sales context."""

    id: str
    name: str | None = None
    stage: str | None = None
    amount: str | None = None


class TicketDTO(BaseModel):
    """A support/inbound ticket — carries the inquiry body for ticket-based inbound."""

    id: str
    subject: str | None = None
    content: str | None = None
    pipeline: str | None = None
    pipeline_stage: str | None = None
    priority: str | None = None
    source_type: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    primary_contact_id: str | None = None
    # HubSpot's own `hs_all_associated_contact_emails`: the address(es) the ticket is
    # held against. This is the one the reply must go to — the operator's rule is that
    # the answer belongs to the ticket, not to whatever the contact record happens to
    # say. HubSpot returns a semicolon/comma-separated list when several contacts are
    # associated; `contact_email` exposes the first one.
    contact_emails: str | None = None

    @property
    def contact_email(self) -> str | None:
        """First address on the ticket, or None when it carries none."""
        raw = (self.contact_emails or "").replace(";", ",")
        for part in raw.split(","):
            cleaned = part.strip()
            if cleaned:
                return cleaned
        return None
