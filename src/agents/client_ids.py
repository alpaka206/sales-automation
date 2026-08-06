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
3. 없으면 새로 발급

2번이 이 파일이 존재하는 이유입니다. 담당자가 바뀌어 다른 사람이 문의해도 같은 고객입니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import func

from ..common.domains import is_personal_domain
from ..common.won import ALLOCATABLE_BANDS
from ..db.models import Client, Contact, Conversation
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

_BAND_SIZE = 1000


def find_existing_client_id(session, contact: Contact | None) -> int | None:
    """이 연락처가 속한 고객사의 번호. 없으면 None."""
    if contact is None:
        return None
    if contact.sheet_client_id:
        return contact.sheet_client_id

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
    return None


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
    for column in (Client.client_id, Conversation.sheet_client_id):
        value = (
            session.query(func.max(column))
            .filter(column >= base, column < ceiling)
            .scalar()
        )
        if value:
            highest = max(highest, int(value))
    return max(highest + 1, base + 1)


def client_id_for_inquiry(conversation_id: int) -> int | None:
    """문의 하나에 줄 Client ID — 같은 고객사가 이미 있으면 그 번호.

    ``inbound.py`` 가 시트에 행을 붙이기 전에 부릅니다. 시트가 죽어 있어도 번호는 나오므로,
    수주 장부는 시트 상태와 무관하게 고객을 하나로 묶을 수 있습니다.
    """
    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            return None
        if conversation.sheet_client_id:
            return conversation.sheet_client_id
        contact = session.get(Contact, conversation.contact_id)
        existing = find_existing_client_id(session, contact)
        if existing:
            logger.info(
                "Client ID %s reused for conversation %s (same customer).",
                existing,
                conversation_id,
            )
            return existing
        return next_client_id(session, "Inbound")
