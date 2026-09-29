"""``conversations.history_requested_at`` — 「이 티켓의 대화를 다시 받아라」 (2026-09-29 감사).

**왜 생겼나.** 티켓 대화 수집의 대기열이 `history_synced_at IS NULL` 한 칸이었고, 웹훅은 그 칸을
지우는 것으로 「다시 받아라」를 말했다. 그런데 수집기가 그 티켓을 **읽는 동안** 고객 메일이 오면 칸이
이미 NULL 이라 웹훅이 할 일이 없고, 읽기가 끝나면 수집기가 「지금」을 찍는다 — 방금 온 메일은 읽은
목록에 없는데 대기열에서도 사라진다. 다음 웹훅이나 사람이 「다시 받기」를 누를 때까지 안 들어왔다.

이제 요청과 도장이 따로 산다. 웹훅은 이 칸에 「지금」을 적고, 수집기는 **읽기 시작한 때**를 도장으로
찍는다. 대기열은 「한 번도 안 받았다 또는 도장 뒤에 요청이 왔다」 — 읽는 도중 온 요청도, 읽다가 죽은
프로세스(OOM)도 대기열에 남는다.

**값은 안 옮긴다.** 지금 NULL 인 도장은 그대로 대기열이다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "conversations" not in set(inspector.get_table_names()):
        return
    if "history_requested_at" in {c["name"] for c in inspector.get_columns("conversations")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE conversations ADD COLUMN history_requested_at TIMESTAMP"))
    logger.info("0128: conversations.history_requested_at 를 만들었습니다.")
