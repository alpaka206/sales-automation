"""나가지 않은 초안(``superseded``)을 지웁니다 — 일회성 정리.

티켓이 New 를 벗어나면 대기 중이던 초안은 뜻을 잃습니다. 답은 다른 경로로 이미 나갔고, 그
초안을 발송 대기에 두면 고객이 받은 답을 한 번 더 보내라고 청하는 셈이기 때문입니다. 지금까지
`stage_sync._retire_superseded_drafts` 는 그 행을 ``superseded`` 로 **닫아** 두었습니다.

두 가지가 문제였습니다:

  * 목록에서 그 행이 **「발송 완료」 묶음**에 들어갔습니다(`messages.LIST_STATUS_BUCKETS`).
    고객이 본 적 없는 글인데 보낸 것으로 보입니다.
  * 리드 히스토리·소통 히스토리를 읽는 사람이 「이 답변은 나갔다」로 셉니다. 그 오해는
    다음 회신의 내용을 바꿉니다 — 「이 얘기는 이미 했으니 생략」이 되어 버립니다.

운영자 지시(2026-08-19): **나가지 않은 초안은 지운다.** 그래서 그 함수는 이제 행을 지우고,
이 이관은 그 전에 쌓인 것을 같이 치웁니다.

**고쳐서 보낸 초안은 여기 안 걸립니다.** 그건 ``sent``/``test_sent`` 이고, 이 이관이 보는
것은 ``superseded`` 한 가지뿐입니다.

승인 기록(`approvals`)을 먼저 지웁니다. FK 는 ON DELETE CASCADE 지만 SQLite 는
`foreign_keys=ON` 일 때만 지키고, 지우는 범위는 눈에 보이는 편이 낫습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    if "messages" not in tables:
        logger.info("0079: messages 없음, 건너뜁니다")
        return
    with engine.begin() as conn:
        doomed = [
            row[0]
            for row in conn.execute(
                text("SELECT id FROM messages WHERE status = 'superseded'")
            )
        ]
        if not doomed:
            logger.info("0079: 종료된 초안이 없습니다.")
            return
        ids = ", ".join(str(int(i)) for i in doomed)
        if "approvals" in tables:
            conn.execute(text(f"DELETE FROM approvals WHERE message_id IN ({ids})"))
        conn.execute(text(f"DELETE FROM messages WHERE id IN ({ids})"))
        logger.info("0079: 나가지 않은 초안 %d건을 지웠습니다.", len(doomed))
