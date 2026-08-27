"""아무도 안 읽는 표 둘을 지웁니다 (2026-08-27 운영자 지시).

**``knowledge_document_revisions``** — 0016 이 만든 뒤 **쓰는 코드도 읽는 코드도 한 줄도
없었습니다.** 그런데 ``KnowledgeDocument`` 의 docstring 이 "every edit snapshots the prior
state into knowledge_document_revisions" 라고 적어 두어서, 이력이 남고 있다고 읽혔습니다.
운영자가 콘솔에서 직접 지웠고, 이 마이그레이션은 다른 배포·개발 DB 를 맞춥니다.

**``llm_usage``** — 반대로 **모든 LLM 호출마다** 한 줄씩 쌓였습니다(``llm/client.py`` 의
``log_usage``). 읽는 곳은 ``report.get_usage_since`` 하나였고 그건 ``POST /run/report`` 로만
불렸는데, 콘솔에 버튼도 스케줄도 없어서 아무도 부르지 않았습니다. 쌓기만 하는 표라 기록
자체를 그만둡니다 — 호출당 INSERT 한 번과 커밋 한 번이 사라집니다.

**되살리려면**: 비용을 다시 보고 싶어지면 표를 되살리기 전에 *어느 화면이 그것을 읽는지*
부터 정하세요. 그게 없어서 이 표가 이렇게 됐습니다. Vertex 콘솔에도 같은 숫자가 있습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_DROP = ("knowledge_document_revisions", "llm_usage")


def up(engine: Engine) -> None:
    existing = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        for table in _DROP:
            if table not in existing:
                logger.info("0095: %s already gone.", table)
                continue
            conn.execute(text(f"DROP TABLE {table}"))
            logger.info("0095: dropped %s.", table)
