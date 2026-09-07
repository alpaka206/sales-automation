"""``policy_sources.scope`` — 어느 회신에 붙는 문서인가 (2026-09-07 운영자 지시).

New 이후의 회신도 초안을 만들게 되면서 (``docs/후속-회신-자동생성-설계.md``) 참고 문서를
갈라 둘 자리가 필요해졌습니다. 운영자의 말: 「new 는 new 에 있는 거 읽으면 되고, 나머지는
new 에 있는 거 읽어도 되는데 **추가적으로 문서를 더 읽어서** 디테일한 내용이 드러나도록.」
즉 첫 회신에는 안 붙고 후속에만 붙는 **깊은 문서**가 따로 있어야 합니다.

**``mode`` 를 늘리지 않는 이유.** 지금 문서를 읽는 자리가 ``mode == 'knowledge'`` 입니다.
``mode`` 를 3~4값으로 늘리면 한 곳이라도 안 고쳤을 때 그 문서가 라우터에서 **조용히
사라집니다** — 화면에는 멀쩡히 있는데 초안이 안 읽습니다. 칸을 따로 두고 기본을 「모두」로
하면, 빠뜨린 자리는 많이 보여 줄 뿐 덜 보여 주지 않습니다. 이 저장소가 같은 이유로 여러 번
당했습니다(0097 의 재우다 만 사본, 0104 의 안 읽히는 거울).

값 셋: ``all``(기본) · ``first``(첫 회신에만) · ``followup``(후속에만). ``mode='rules'``
행에서는 뜻이 없습니다 — 그쪽은 고르는 대상이 아니라 모든 프롬프트에 통째로 들어갑니다.

기존 행은 전부 ``all`` 이라 **오늘 동작이 안 바뀝니다.**
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "policy_sources" not in set(inspector.get_table_names()):
        logger.info("0108: policy_sources 없음, 건너뜁니다.")
        return
    columns = {c["name"] for c in inspector.get_columns("policy_sources")}
    if "scope" in columns:
        return
    with engine.begin() as conn:
        # ``server_default`` 를 답니다 — 씨앗 마이그레이션들이 이 표에 raw SQL 로 넣는데
        # 그 INSERT 는 이 열을 모릅니다(``version`` 이 같은 이유로 그렇게 되어 있습니다).
        conn.execute(
            text("ALTER TABLE policy_sources ADD COLUMN scope VARCHAR(16) "
                 "NOT NULL DEFAULT 'all'")
        )
    logger.info("0108: policy_sources.scope 를 더했습니다 (기존 행은 전부 'all').")
