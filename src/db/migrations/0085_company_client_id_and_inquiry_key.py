"""Client ID는 회사 키로 공유하고, 문의 행에는 별도 고유 키를 둡니다.

기존에는 ``conversations.sheet_client_id`` 자체가 UNIQUE라 같은 회사의 두 번째 문의가
같은 Client ID를 쓸 수 없었습니다. 그 제약을 풀고, 정렬에도 안전한 문의별 ``Inquiry ID``를
추가합니다. 기존 행은 HubSpot ticket id가 있으면 그것으로, 없으면 로컬 conversation id로
결정적으로 채웁니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "conversations" not in set(inspector.get_table_names()):
        logger.info("0085: conversations 없음, 건너뜁니다")
        return

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    with engine.begin() as conn:
        if "sheet_inquiry_key" not in columns:
            conn.execute(
                text("ALTER TABLE conversations ADD COLUMN sheet_inquiry_key VARCHAR(128)")
            )

        rows = conn.execute(
            text(
                "SELECT id, hubspot_ticket_id FROM conversations "
                "WHERE sheet_inquiry_key IS NULL"
            )
        ).fetchall()
        for row in rows:
            inquiry_key = (
                f"hubspot:{row.hubspot_ticket_id}"
                if row.hubspot_ticket_id
                else f"conversation:{row.id}"
            )
            conn.execute(
                text(
                    "UPDATE conversations SET sheet_inquiry_key = :key WHERE id = :id"
                ),
                {"key": inquiry_key, "id": row.id},
            )

        # 0035 made Client ID the inquiry key. It is now deliberately non-unique.
        conn.execute(text("DROP INDEX IF EXISTS ux_conversations_sheet_client_id"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_conversations_sheet_inquiry_key "
                "ON conversations (sheet_inquiry_key)"
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_conversations_sheet_inquiry_key "
                "ON conversations (sheet_inquiry_key)"
            )
        )
    logger.info("0085: Client ID 공유를 허용하고 문의별 Inquiry ID를 추가했습니다")
