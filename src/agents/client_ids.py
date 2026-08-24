"""Client ID 발급 — **고객사 하나에 하나**.

전에는 문의 하나에 하나였습니다. ``suggest_inbound_client_id()`` 가 Inbound DB 의 다음
번호를 가져오고, ``inbound.py`` 가 그 값을 **대화마다** 넣었습니다. 그래서 같은 회사가 두 번
문의하면 Client ID 가 두 개 생겼고, 수주 장부를 그 위에 세우면 한 고객의 계약과 크레딧과
소통 히스토리가 두 갈래로 갈라집니다. 나중에 합치는 것은 훨씬 비쌉니다.

이제 새 문의가 들어오면 **먼저 같은 고객사를 찾습니다.** 찾으면 그 번호를 그대로 씁니다.

무엇을 "같은 고객사"로 볼지가 전부인데, 순서대로 봅니다:

1. 그 연락처가 이미 들고 있는 번호 (같은 사람이 또 문의한 경우)
2. 같은 회사 도메인의 다른 연락처가 들고 있는 번호 — **개인 메일 도메인은 제외**합니다.
   gmail 로 문의한 두 사람을 한 회사로 묶으면 남의 계약이 보입니다.
3. 수주 장부에 회사명이 정확히 일치하는 고객이 **하나뿐이면** 그 번호
4. 없거나 동명 고객이 여러 명이면 새로 발급

2번이 이 파일이 존재하는 이유입니다. 담당자가 바뀌어 다른 사람이 문의해도 같은 고객입니다.
"""

from __future__ import annotations

import re

from sqlalchemy import func

from ..common.domains import is_personal_domain
from ..common.won import ALLOCATABLE_BANDS
from ..db.models import Client, Contact, Conversation

_BAND_SIZE = 1000
_PLACEHOLDER_COMPANIES = {"", "unknown", "알수없음", "고객사미확인", "미확인"}


def company_key(value: str | None) -> str:
    """대소문자·공백·구두점을 제외한 회사명 비교 키."""
    return re.sub(r"[^a-z0-9가-힣]", "", (value or "").casefold())


def unique_client_id_for_company(session, company: str | None) -> int | None:
    """회사명이 같은 수주 고객이 정확히 하나일 때만 그 Client ID를 돌려줍니다."""
    key = company_key(company)
    if key in _PLACEHOLDER_COMPANIES:
        return None
    matches = {
        int(client_id)
        for client_id, stored_company in session.query(
            Client.client_id, Client.company
        ).all()
        if company_key(stored_company) == key
    }
    return next(iter(matches)) if len(matches) == 1 else None


def find_existing_client_id(session, contact: Contact | None) -> int | None:
    """이 연락처가 속한 고객사의 번호. 없으면 None."""
    if contact is None:
        return None
    if contact.sheet_client_id:
        return contact.sheet_client_id

    # Legacy data may have the number only on an older conversation. The same
    # person is still the same customer even before domain-level matching.
    own_inquiry = (
        session.query(Conversation.sheet_client_id)
        .filter(
            Conversation.contact_id == contact.id,
            Conversation.sheet_client_id.isnot(None),
        )
        .order_by(Conversation.sheet_client_id)
        .first()
    )
    if own_inquiry and own_inquiry[0]:
        return int(own_inquiry[0])

    # 같은 회사 도메인의 다른 담당자. 개인 메일 도메인은 회사가 아닙니다.
    domain = (contact.domain or "").lower().strip()
    if domain and not is_personal_domain(domain):
        sibling = (
            session.query(Contact.sheet_client_id)
            .filter(
                Contact.domain == domain,
                Contact.id != contact.id,
                Contact.sheet_client_id.isnot(None),
            )
            .order_by(Contact.sheet_client_id)
            .first()
        )
        if sibling and sibling[0]:
            return int(sibling[0])
    return unique_client_id_for_company(session, contact.company)


def next_client_id(session, customer_type: str) -> int:
    """그 번호대의 다음 번호.

    ``clients`` 와 ``conversations`` 를 **둘 다** 봅니다. 인바운드 번호는 문의 시점에
    나가므로 아직 수주 고객이 아닌 번호가 대화에만 있고, 반대로 Outbound 고객은 대화가
    없습니다. 한쪽만 보면 이미 쓰는 번호를 다시 내줍니다.
    """
    base = ALLOCATABLE_BANDS.get(customer_type)
    if base is None:
        raise ValueError(f"발급할 수 없는 고객 종류입니다: {customer_type}")
    ceiling = base + _BAND_SIZE
    highest = 0
    # Contact is included because a company may already own an ID even when its
    # original conversation was removed or has not yet become a won Client.
    for column in (Client.client_id, Contact.sheet_client_id, Conversation.sheet_client_id):
        value = (
            session.query(func.max(column))
            .filter(column >= base, column < ceiling)
            .scalar()
        )
        if value:
            highest = max(highest, int(value))
    return max(highest + 1, base + 1)
