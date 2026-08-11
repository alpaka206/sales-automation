"""플랜 상태를 저장하지 않습니다 — 계약 기간이 정합니다.

`clients.plan_status` 는 사람이 골라 넣는 값이었습니다. 그래서 계약이 끝나도 누가 손으로
바꿔 주기 전까지 「사용중」으로 남았고, 그 값이 그대로 워크북 J열까지 실려 나갔습니다.
이제 `won.plan_status` 가 오늘과 계약 기간을 비교해 정합니다:

- 오늘이 어느 계약 기간 안 → 사용중
- 아직 시작 전이거나 날짜가 덜 적힌 계약이 있음 → 세팅중
- 있는 계약이 전부 지남 → 사용 중단
- 계약이 아직 없음 → 세팅중

고객 종류를 저장하지 않는 것과 같은 이유입니다: 날짜에서 나오는 값을 따로 들고 있으면
반드시 어긋나고, 어긋난 뒤에는 어느 쪽이 맞는지 아무도 모릅니다. 열을 남겨 두면 다음
사람이 그걸 읽습니다.

화면의 고르개(고객 상세 「플랜 상태」, 계약 폼 「저장 후 플랜 상태」)와 시트에서 J열을
읽어 오던 코드도 같이 지웠습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "clients" not in set(inspector.get_table_names()):
        logger.info("0067: clients 테이블이 없어 건너뜁니다.")
        return
    if "plan_status" not in {c["name"] for c in inspector.get_columns("clients")}:
        return
    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE clients DROP COLUMN plan_status"))
        except Exception:
            # 아주 오래된 SQLite. 읽는 곳이 없으므로 남아 있어도 무해합니다.
            logger.warning("0067: clients.plan_status 를 지우지 못했습니다 — 그대로 둡니다.")
            return
    logger.info("0067: 플랜 상태는 이제 계약 기간에서 나옵니다.")
