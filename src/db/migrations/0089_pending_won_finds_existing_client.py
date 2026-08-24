"""Client ID가 비어 있는 수주 전환 대기를 기존 고객과 안전하게 연결합니다.

문의·연락처가 이미 번호를 가진 경우에는 그 번호를 쓰고, 둘 다 비어 있으면 회사명이
정확히 일치하는 수주 고객이 하나뿐일 때만 연결합니다. 동명 고객이 여러 명이거나 회사명이
미확인이면 그대로 두어 계약 등록 화면에서 사람이 결정하게 합니다.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)
_PLACEHOLDERS = {"", "unknown", "알수없음", "고객사미확인", "미확인"}


def _company_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", (value or "").casefold())


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    required = {"pending_won", "clients", "conversations", "contacts"}
    if not required.issubset(tables):
        return

    changed = 0
    with engine.begin() as conn:
        company_clients: dict[str, set[int]] = {}
        for row in conn.execute(text("SELECT client_id, company FROM clients")).mappings():
            key = _company_key(row["company"])
            if key not in _PLACEHOLDERS:
                company_clients.setdefault(key, set()).add(int(row["client_id"]))

        rows = conn.execute(
            text(
                "SELECT p.id, p.company, p.conversation_id, "
                "c.contact_id, c.sheet_client_id AS conversation_client_id, "
                "ct.company AS contact_company, "
                "ct.sheet_client_id AS contact_client_id "
                "FROM pending_won p "
                "LEFT JOIN conversations c ON c.id = p.conversation_id "
                "LEFT JOIN contacts ct ON ct.id = c.contact_id "
                "WHERE p.status = 'pending' AND p.client_id IS NULL"
            )
        ).mappings()
        for row in rows:
            client_id = row["conversation_client_id"] or row["contact_client_id"]
            if client_id is None:
                candidates: set[int] = set()
                for company in (row["company"], row["contact_company"]):
                    key = _company_key(company)
                    if key not in _PLACEHOLDERS:
                        candidates.update(company_clients.get(key, set()))
                if len(candidates) == 1:
                    client_id = next(iter(candidates))
            if client_id is None:
                continue

            conn.execute(
                text("UPDATE pending_won SET client_id = :client_id WHERE id = :id"),
                {"client_id": int(client_id), "id": row["id"]},
            )
            if row["conversation_id"] is not None:
                conn.execute(
                    text(
                        "UPDATE conversations SET sheet_client_id = :client_id "
                        "WHERE id = :id AND sheet_client_id IS NULL"
                    ),
                    {"client_id": int(client_id), "id": row["conversation_id"]},
                )
            if row["contact_id"] is not None:
                conn.execute(
                    text(
                        "UPDATE contacts SET sheet_client_id = :client_id "
                        "WHERE id = :id AND sheet_client_id IS NULL"
                    ),
                    {"client_id": int(client_id), "id": row["contact_id"]},
                )
            changed += 1

    logger.info("0089: 수주 전환 대기 Client ID %d건을 안전하게 연결했습니다", changed)
