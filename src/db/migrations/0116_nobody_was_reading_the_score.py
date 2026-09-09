"""리드 점수와 리드 온도를 지웁니다 (2026-09-09 운영자 지시).

세 칸입니다 — `contacts.score` · `messages.score_snapshot` · `customer_profiles.
lead_temperature`.

**읽는 코드가 있는 칸만 남긴다**(0101)의 연장입니다. 세어 봤습니다:

- `contacts.score` — 쓰는 곳 한 곳, **읽는 곳 0**.
- `messages.score_snapshot` — API 가 내려보내고 화면의 타입 선언에도 있는데, 그 값을
  **그리는 JSX 가 없었습니다.** 즉 왕복만 하고 아무 데도 안 뜹니다.
- `customer_profiles.lead_temperature` — 세 화면에 뜨긴 했는데 전부 읽기 전용이고, 채우는
  길이 고객 상세의 폼 하나였습니다. 운영자가 안 쓰는 값입니다.

**점수 쪽은 칸만의 문제가 아니었습니다.** 그 숫자를 만들려고 문의마다 Gemini 왕복이
하나 더 났고(`inbound/score_adjust`), 그 결과가 실제로 쓰이는 자리는 초안 프롬프트의
「점수: 80」 **한 줄**이었습니다 — 사람은 아무도 안 봤습니다. 512Mi 인스턴스에서 그
왕복 하나가 공짜가 아닙니다(같은 날 OOM 두 건).

되살리려면 **어느 화면이 그 값을 읽는지부터 정하세요.** 그게 없어서 이렇게 됐습니다 —
`llm_usage`(0095)와 `customer_profiles.qualification`(0104)이 지나간 그 자리입니다.

**SQLite 도 열을 지울 수 있습니다** (3.35+, 2021). 이 저장소의 다른 DROP COLUMN 이관과
같은 방식이고, 없는 열을 지우려 하면 조용히 넘어갑니다 — 이관이 반쯤 돌던 DB 에서도
다시 돌 수 있어야 합니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_DROPS = (
    ("contacts", "score"),
    ("messages", "score_snapshot"),
    ("customer_profiles", "lead_temperature"),
)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, column in _DROPS:
        if table not in tables:
            continue
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            continue
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        logger.info("0116: %s.%s 를 지웠습니다.", table, column)
