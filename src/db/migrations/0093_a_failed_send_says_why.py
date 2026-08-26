"""발송이 실패하면 왜 실패했는지도 같이 남긴다.

`send_worker` 는 실패한 메시지에 `status="send_failed"` 하나만 쓰고 사유는 로그에만
남겼습니다. 화면에는 빨간 「발송 실패」 배지만 서고, 그 옆에 이유를 적을 자리가 없었습니다.
운영자가 실제로 겪은 일이 이것입니다(2026-08-26, msg 62): 메일이 안 왔는데 화면은 실패라고만
하고, 왜인지는 Render 로그를 뒤져야 나왔습니다 — 그리고 로그는 30분이면 스크롤 밖으로
밀립니다.

**새 열이 필요한 이유**: 이미 있는 `post_send_sync_error` 는 「메일은 나갔고 기록만
실패했다」는 뜻이라 여기에 쓸 수 없습니다. 한 칸에 두 사건을 담으면 복구 화면의 두 목록이
같은 값을 다른 뜻으로 읽습니다.

복구 화면은 이미 그 값을 빨간 줄로 그리고 있습니다(`Recovery.tsx` 의 `row.error`) —
지금까지 `send_failed` 행에서만 늘 비어 있었을 뿐입니다. 그래서 프런트 수정이 없습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "messages" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("messages")}
    if "send_error" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE messages ADD COLUMN send_error TEXT"))
    logger.info("0093: messages.send_error added — a failed send now records its reason")
