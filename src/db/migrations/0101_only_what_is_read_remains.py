"""읽는 코드가 있는 칸만 남깁니다 (2026-08-27 운영자 지시).

## ``policy_sources``

``order_index`` — 「항상 적용」 규칙이 시스템 지시문에 들어가는 **순서**였습니다. 읽기는
했지만(``prompts._rules_from_db``) **정할 방법이 없었습니다**: 만들기 라우트도 수정 라우트도
이 칸을 안 받아서 운영의 세 행이 전부 ``100`` 이었고, 결국 순서는 ``id`` 가 정했습니다.
이제 그것을 사실대로 적습니다 — ``id`` 순, 곧 만든 순서입니다.

``status`` — **오늘 아침까지는 살아 있던 칸입니다.** 지운 문서를 초안이 안 보게 하는 유일한
장치였는데, 0100 이 삭제를 하드 삭제로 바꾸면서 ``deleted`` 가 되는 행 자체가 없어졌습니다.
표에 있는 행이 곧 살아 있는 행이므로 「항상 쓰는 것이니 항상 가져옵니다」.
``email_templates.status`` 와 ``document_revisions.status`` 도 같은 이유로 나갑니다.

``summary`` — **읽는 코드가 없고 채워진 행도 0개**였습니다. 라우터가 읽는 요약은
``usage_note``(「언제 쓰는가」)이고, 비면 본문 앞 400자입니다(``knowledge.summary_of``).
노션에서 받아오던 시절의 유물입니다.

``effective_on`` · ``edited_at`` — 「언제 기준인가」를 두 칸이 서로 다르게 말하고 있었고,
그나마 ``effective_on`` 은 마이그레이션이 심은 한 행 말고는 아무도 안 채웠습니다. 답은
``updated_at`` 하나입니다 — 저장할 때마다 자동으로 움직이고, 사람이 안 채워도 늘 맞습니다.

## 옛 마이그레이션

0043~0064 의 씨앗들은 ``order_index``·``status``·``summary``·``effective_on``·``edited_at``
을 이름으로 INSERT 하고, 0019~0056 은 ``status`` 를 씁니다. 여덟 개 넘는 파일의 SQL 을 고쳐
역사를 다시 쓰는 대신, **0043 과 0019 가 그때의 칸을 세워 두고 여기서 그때처럼 지웁니다.**
이미 적용된 DB 에서는 칸이 있으므로 그대로 지워집니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_DROP = {
    "policy_sources": ("order_index", "status", "summary", "effective_on", "edited_at"),
    "email_templates": ("status",),
    "document_revisions": ("status",),
}


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, columns in _DROP.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for column in columns:
                if column not in existing:
                    continue
                try:
                    conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
                    logger.info("0101: %s.%s 지웠습니다.", table, column)
                except Exception:
                    # 아주 오래된 SQLite 는 DROP COLUMN 이 없습니다. 개발용 파일 DB 에서만
                    # 나올 수 있고, 칸이 남아 있어도 코드가 안 읽으므로 치명적이지 않습니다.
                    logger.warning("0101: %s.%s 를 못 지웠습니다 (무시).", table, column, exc_info=True)
