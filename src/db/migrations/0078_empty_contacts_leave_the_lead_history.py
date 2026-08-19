"""아무것도 안 남은 연락처를 리드 히스토리에서 지웁니다 — 일회성 정리.

티켓이 사라지면 대화·메시지가 같이 갔고, 연락처만 껍데기로 남았습니다. 리드 히스토리 목록을
열면 대화도 계약도 메모도 없는 줄이 서 있었고(운영 실측 2026-08-19: 288줄 중 6줄), 눌러 봐야
아무것도 없습니다. 운영자 지시로 지웁니다.

앞으로 생기는 것은 `hubspot_reconcile.delete_conversation` 이 그 자리에서 막습니다 — 티켓이
사라질 때 남을 것이 없으면 연락처까지 지웁니다. 이 이관은 **그 코드가 생기기 전에 이미 쌓인
것**만 치웁니다.

지우는 조건은 넷 다 0 일 때입니다:

  * 대화(`conversations`) — 다른 티켓이 하나라도 있으면 그 사람은 살아 있는 리드입니다
  * 소통 히스토리(`customer_interactions`) — 사람이 손으로 쓴 기록
  * 계약 기록(`contract_records`) — 돈
  * 수주 고객(`clients.contact_id`) — 연결이 있으면 수주 고객입니다

`customer_profiles` 는 명시적으로 같이 지웁니다. FK 는 ON DELETE CASCADE 지만 SQLite 는
`foreign_keys=ON` 일 때만 지키고, 남으면 없는 연락처를 가리키는 행이 됩니다.

**무엇을 지웠는지 이름으로 남깁니다.** 지운 뒤에는 확인할 방법이 없고, 「왜 그 고객이
없어졌지」는 나중에 반드시 나오는 질문입니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

# 이 이관이 사는 동안 스키마가 바뀌어도 안전하게: 없는 표는 조건에서 빼고 셉니다.
_GUARD_TABLES = {
    "conversations": "contact_id",
    "customer_interactions": "contact_id",
    "contract_records": "contact_id",
    "clients": "contact_id",
}


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    if "contacts" not in tables:
        logger.info("0078: contacts 없음, 건너뜁니다")
        return

    guards = [
        f"NOT EXISTS (SELECT 1 FROM {table} WHERE {table}.{column} = contacts.id)"
        for table, column in _GUARD_TABLES.items()
        if table in tables
    ]
    where = " AND ".join(guards)

    with engine.begin() as conn:
        doomed = conn.execute(
            text(f"SELECT id, full_name, email FROM contacts WHERE {where} ORDER BY id")
        ).fetchall()
        if not doomed:
            logger.info("0078: 비어 있는 연락처가 없습니다.")
            return
        ids = [row[0] for row in doomed]
        placeholders = ", ".join(str(int(i)) for i in ids)
        if "customer_profiles" in tables:
            conn.execute(
                text(f"DELETE FROM customer_profiles WHERE contact_id IN ({placeholders})")
            )
        conn.execute(text(f"DELETE FROM contacts WHERE id IN ({placeholders})"))
        logger.info(
            "0078: 빈 연락처 %d명을 지웠습니다 — %s",
            len(doomed),
            " · ".join(f"#{row[0]} {row[1] or '이름없음'} <{row[2] or '-'}>" for row in doomed),
        )
