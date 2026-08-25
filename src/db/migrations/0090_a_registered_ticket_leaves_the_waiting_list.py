"""이미 계약이 등록된 티켓을 수주 전환 대기에서 내립니다.

티켓은 계약에 붙는 값입니다(``client_contracts.ticket_id``). 거기 있으면 그 문의는 계약
정보를 이미 받은 것이라 「계약 정보를 입력해야 합니다」 카드에 있을 이유가 없습니다.

왜 남아 있었나: 대기를 닫는 길이 **대기 카드에서 계약 폼으로 들어오는 것 하나**뿐이었습니다.
계약이 시트에서 들어왔거나(``sheet_to_db``) 운영자가 계약에 티켓을 손으로 적은 건은 그 길을
지난 적이 없어 ``done`` 행이 없고, 그래서 백필과 10분 스윕이 그 티켓을 훑을 때마다 이미
등록된 고객이 대기 목록으로 돌아왔습니다. 런타임 쪽은 ``stage_sync._enqueue_pending_won``
과 ``won_customers._claim_ticket`` 이 막습니다 — 이 이관은 그 전에 쌓인 행을 치웁니다.

같이 묶습니다: 그 문의와 연락처의 Client ID 가 비어 있으면 계약의 고객으로 채웁니다.
비어 있지 않으면 **건드리지 않습니다** — 이미 다른 번호로 살아 있는 연결을 이관이 조용히
옮기면 그 고객의 소통 히스토리가 통째로 다른 장부로 갑니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    if not {"pending_won", "client_contracts"}.issubset(tables):
        return

    closed = 0
    with engine.begin() as conn:
        # 같은 티켓에 계약이 둘일 수는 없지만(운영자가 적는 값이라 막혀 있지는 않습니다),
        # 그럴 때는 차수가 빠른 쪽 — 1차 계약이 그 티켓에서 나온 계약입니다.
        owner: dict[str, int] = {}
        rows = conn.execute(
            text(
                "SELECT ticket_id, client_id, seq FROM client_contracts "
                "WHERE ticket_id IS NOT NULL AND ticket_id <> '' "
                "ORDER BY seq DESC"
            )
        ).mappings()
        for row in rows:
            owner[str(row["ticket_id"]).strip()] = int(row["client_id"])

        if not owner:
            return

        pending = conn.execute(
            text(
                "SELECT p.id, p.ticket_id, p.conversation_id, c.contact_id "
                "FROM pending_won p "
                "LEFT JOIN conversations c ON c.id = p.conversation_id "
                "WHERE p.status = 'pending'"
            )
        ).mappings()
        for row in pending:
            client_id = owner.get(str(row["ticket_id"] or "").strip())
            if client_id is None:
                continue
            conn.execute(
                text(
                    "UPDATE pending_won SET client_id = :client_id, status = 'done' "
                    "WHERE id = :id"
                ),
                {"client_id": client_id, "id": row["id"]},
            )
            if row["conversation_id"] is not None:
                conn.execute(
                    text(
                        "UPDATE conversations SET sheet_client_id = :client_id "
                        "WHERE id = :id AND sheet_client_id IS NULL"
                    ),
                    {"client_id": client_id, "id": row["conversation_id"]},
                )
            if row["contact_id"] is not None:
                conn.execute(
                    text(
                        "UPDATE contacts SET sheet_client_id = :client_id "
                        "WHERE id = :id AND sheet_client_id IS NULL"
                    ),
                    {"client_id": client_id, "id": row["contact_id"]},
                )
            closed += 1

    logger.info("0090: 계약이 이미 있는 수주 전환 대기 %d건을 내렸습니다", closed)
