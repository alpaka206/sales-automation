"""지우면 **행이 사라집니다** — 그리고 안 쓰는 칸 넷을 지웁니다 (2026-08-27 운영자 지시).

## 왜 하드 삭제인가 — 소프트 삭제가 키를 영영 붙들고 있었다

``email_templates.key`` 와 ``policy_sources.doc_key`` 는 **unique** 이고, 만들기 라우트는
상태를 안 보고 중복을 막습니다. 여기까지는 옛날에도 같았지만 그때는 7일 뒤 청소가 있어서
키가 결국 풀렸습니다. 그 청소를 없애면서(「지운 것은 DB 에 영원히 남는다」) **키를 영원히
붙들고 있는 행**이 생겼습니다: `reply_format` 을 한 번 지우면 다시는 그 이름으로 만들 수
없고, 그 이름은 발송 경로가 찾는 이름입니다. 정책 문서도 같습니다 — `doc_key` 가 제목에서
나오므로 「CS 문의 대응 가이드」를 지우면 그 제목을 다시는 못 씁니다.

운영자가 제안한 대로 고칩니다: **지우면 행이 사라지고, 그때의 내용은 판본 이력
(``document_revisions``)에 남습니다.** 「DB 에서 볼 수 있게 영원히 지우지 않는다」는 그
이력이 지킵니다 — 지우기 **직전** 스냅샷이 `change_note='deleted'` 로 이미 들어갑니다.
그러면 ``deleted_at`` 도, 「7일」도, 되돌리기도 필요 없습니다.

## 지우는 칸

``email_templates.subject`` — **운영 7행 중 채워진 행이 0개**이고 **읽는 코드가 없습니다.**
``get_email_template`` 은 ``body`` 만 돌려줍니다. 접수확인 템플릿이 쓰던 칸인데 그 기능은
0087 에서 나갔습니다.

``email_templates.channel`` — 행마다 ``'email'``. 읽는 곳은 ``list_signature_templates`` 의
``channel == 'email'`` 하나이고, 값이 하나뿐이라 한 번도 아무것도 안 걸렀습니다.

``email_templates.deleted_at`` · ``policy_sources.deleted_at`` — 위 이유로 쓸 일이
없어졌습니다. 운영 데이터에도 채워진 행이 0개입니다.

``author`` 는 **남깁니다 — 뜻이 바뀝니다.** 만든 사람이 아니라 **마지막으로 저장한
사람**이고, 목록의 수정일 옆에 뜹니다. 지금까지 아무도 안 읽던 칸입니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_DROP = {
    "email_templates": ("subject", "channel", "deleted_at"),
    "policy_sources": ("deleted_at",),
}


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        # 소프트 삭제로 남아 있던 행은 이제 뜻이 없습니다 — 화면에도 안 뜨고, 키만
        # 붙들고 있습니다. 지우기 전 내용은 그때 남긴 판본 이력에 있습니다.
        for table in ("email_templates", "policy_sources"):
            if table not in tables:
                continue
            removed = conn.execute(
                text(f"DELETE FROM {table} WHERE status = 'deleted'")
            ).rowcount or 0
            if removed:
                logger.info("0100: %s 에서 소프트 삭제 행 %d개를 지웠습니다.", table, removed)

        for table, columns in _DROP.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for column in columns:
                if column not in existing:
                    continue
                try:
                    conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
                    logger.info("0100: %s.%s 지웠습니다.", table, column)
                except Exception:
                    # 아주 오래된 SQLite 는 DROP COLUMN 이 없습니다. 개발용 파일 DB 에서만
                    # 나올 수 있고, 칸이 남아 있어도 코드가 안 읽으므로 치명적이지 않습니다.
                    logger.warning("0100: %s.%s 를 못 지웠습니다 (무시).", table, column, exc_info=True)
