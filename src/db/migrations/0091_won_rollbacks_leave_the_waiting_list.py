"""Won 에서 이미 벗어난 티켓을 수주 전환 대기에서 내립니다.

런타임 쪽은 ``stage_sync._retire_pending_won`` 이 막습니다 — 단계를 옮기는 여덟 경로가
전부 ``_retire_superseded_drafts`` 를 지나므로 거기 달려 있습니다. 이 이관은 **그 전에
되돌려진 건**을 치웁니다: 10분 스윕은 허브스팟에서 **최근에 바뀐** 티켓만 훑으므로,
오래전에 Won 을 벗어나 그 뒤로 안 건드려진 티켓은 스스로 걸리지 않습니다.

``dismissed`` 이지 ``done`` 이 아닙니다 — ``done`` 은 「계약을 받았다」라서, 그것으로 닫으면
그 티켓이 다시 Won 이 되어도 카드가 안 돌아옵니다.

딸려 있던 **계약 없는 고객**은 여기서 손대지 않습니다. 이 이관이 처음 쓰였을 때는 그런
고객을 지웠는데, 지우면 Client ID 가 같이 사라집니다 — 그 번호는 문의·연락처가 들고 있고
워크북의 계약·회차 탭과 Inbound DB 가 그 행을 조회해 회사명을 가져옵니다. 운영자 지시로
방향을 바꿨습니다(2026-08-25): 지우지 않고 **장부에서 내립니다**. 그 칸(`clients.retired_on`)은
다음 이관이 만들고, 내리는 것도 거기서 합니다.
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
    dismissed = 0
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

    logger.info("0091: Won 을 벗어난 수주 전환 대기 %d건을 내렸습니다", dismissed)
