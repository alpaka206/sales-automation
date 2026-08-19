"""No Response 단계가 없어졌습니다. 그 값을 쓰던 행은 Concluded 로 접습니다.

2026-08-19, 허브스팟 파이프라인 정리:

- **Not a Fit → Concluded.** 이름만 바뀌었고 stage id(1404814097)는 그대로라 로컬 키는
  ``closed`` 그대로입니다. 여기서 옮길 데이터가 없습니다.
- **No Response 는 사라졌습니다.** 이름이 바뀐 것이 아니라 단계 자체가 없어졌으므로,
  그 값을 들고 있는 행은 이제 화면 어느 열에도 안 서고 어느 필터에도 안 걸립니다.

그래서 ``no_response`` → ``closed`` 로 접습니다. 「답이 없어 끝난 문의」와 「우리 건이
아니어서 끝난 문의」는 둘 다 **끝난 문의**이고, 두 단계가 하나로 합쳐진 것이 이번 변경의
내용이기도 합니다(그쪽 이름이 Not a Fit 에서 Concluded 로 넓어진 이유). ``STATE_FOR_STAGE``
에서도 둘 다 ``lost`` 였으므로 고객 상태는 그대로입니다.

0040 과 같은 방식입니다: 두 열 다 CHECK 도 enum 도 없는 문자열이라 DDL 이 없고, 다시
돌려도 걸리는 행이 없어 멱등합니다(``migrate.py`` 가 ``up()`` 과 기록을 따로 커밋하고
CI 가 ``init_db.py`` 를 두 번 돌립니다).

``contacts.lifecycle_stage``(허브스팟 자기 어휘)와 ``customer_profiles.customer_state``
는 건드리지 않습니다 — 이름이 겹쳐 보여도 다른 칸입니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

OLD, NEW = "no_response", "closed"

# 스키마에 단계가 사는 열은 이 둘뿐입니다. 하나만 옮기면 보드와 고객 상세가 서로 다른
# 단계를 보여 주고, 그 어긋남은 다음 동기화가 고쳐 주지 않습니다.
_COLUMNS = (
    ("conversations", "stage"),
    ("customer_profiles", "pipeline_stage"),
)


def up(engine: Engine) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column in _COLUMNS:
            if table not in tables:
                logger.info("0076: %s 없음, 건너뜁니다", table)
                continue
            result = conn.execute(
                text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old"),
                {"new": NEW, "old": OLD},
            )
            if result.rowcount:
                logger.info(
                    "0076: %s.%s %s -> %s (%s행)", table, column, OLD, NEW, result.rowcount
                )
