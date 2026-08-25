"""Won 에서 이미 벗어난 티켓을 수주 전환 대기에서 내립니다.

런타임 쪽은 ``stage_sync._retire_pending_won`` 이 막습니다 — 단계를 옮기는 여덟 경로가
전부 ``_retire_superseded_drafts`` 를 지나므로 거기 달려 있습니다. 이 이관은 **그 전에
되돌려진 건**을 치웁니다: 10분 스윕은 허브스팟에서 **최근에 바뀐** 티켓만 훑으므로,
오래전에 Won 을 벗어나 그 뒤로 안 건드려진 티켓은 스스로 걸리지 않습니다.

``dismissed`` 이지 ``done`` 이 아닙니다 — ``done`` 은 「계약을 받았다」라서, 그것으로 닫으면
그 티켓이 다시 Won 이 되어도 카드가 안 돌아옵니다.

딸려 있던 **계약 없는 고객**도 같이 내립니다. 조건은 런타임(``_drop_empty_client``)과
같습니다: 계약이 하나라도 있거나, 그 번호를 쓰는 다른 문의·대기·계약 기록이 있으면 그대로
둡니다. 문의·연락처에 박힌 번호는 건드리지 않습니다 — 그 번호는 그 회사 것이고, 비우면
같은 회사가 다음에 수주됐을 때 새 번호가 나가 한 회사에 번호가 둘 생깁니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

# `stage_sync.LOCAL_STAGE_TO_SETTING` 에서 won 을 뺀 것. 여기 적어 두는 이유는 이관이
# 그때의 규칙을 박제해야 하기 때문입니다 — 나중에 단계가 늘거나 이름이 바뀌어도 이미
# 적용된 이관의 결과가 달라지면 안 됩니다.
_LEFT_WON = ("new", "meeting_link_sent", "negotiation", "reminder_sent", "closed_lost", "closed")


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    if not {"pending_won", "conversations", "clients"}.issubset(tables):
        return

    stages = ", ".join(f"'{stage}'" for stage in _LEFT_WON)
    dismissed = dropped = 0
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT p.id, p.client_id, p.conversation_id "
                "FROM pending_won p "
                "JOIN conversations c ON c.id = p.conversation_id "
                f"WHERE p.status = 'pending' AND c.stage IN ({stages})"
            )
        ).mappings().all()

        for row in rows:
            conn.execute(
                text("UPDATE pending_won SET status = 'dismissed' WHERE id = :id"),
                {"id": row["id"]},
            )
            dismissed += 1

        # 상태를 다 바꾼 **뒤에** 고객을 봅니다. 먼저 보면, 같은 번호를 쓰는 다른 대기 행이
        # 아직 'pending' 이라 「남이 쓰는 중」으로 읽혀 아무것도 못 내립니다.
        for row in rows:
            client_id = row["client_id"]
            if not client_id:
                continue
            args = {"cid": int(client_id), "pid": row["id"], "conv": row["conversation_id"]}
            busy = conn.execute(
                text(
                    "SELECT 1 FROM client_contracts WHERE client_id = :cid "
                    "UNION ALL "
                    "SELECT 1 FROM conversations "
                    "WHERE sheet_client_id = :cid AND (:conv IS NULL OR id <> :conv) "
                    "UNION ALL "
                    "SELECT 1 FROM pending_won "
                    "WHERE client_id = :cid AND id <> :pid AND status IN ('pending', 'done') "
                    + (
                        "UNION ALL SELECT 1 FROM contract_records WHERE sheet_client_id = :cid"
                        if "contract_records" in tables
                        else ""
                    )
                    + " LIMIT 1"
                ),
                args,
            ).first()
            if busy:
                continue
            deleted = conn.execute(
                text("DELETE FROM clients WHERE client_id = :cid"), {"cid": args["cid"]}
            )
            dropped += deleted.rowcount or 0

    logger.info(
        "0091: Won 을 벗어난 수주 전환 대기 %d건을 내리고, 계약 없는 고객 %d건을 같이 치웠습니다",
        dismissed,
        dropped,
    )
