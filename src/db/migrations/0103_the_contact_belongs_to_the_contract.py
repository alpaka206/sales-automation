"""고객사 측 담당자·연락처를 **계약**으로 옮깁니다 (2026-08-31 운영자 지시).

담당자는 계약마다 다를 수 있습니다 — 부서가 다르거나, 재계약 때 사람이 바뀝니다. 고객
행에 한 벌만 두면 두 번째 계약을 맺는 순간 첫 계약의 담당자가 덮여 **사라집니다**. 되돌릴
방법이 없고, 화면에는 「담당자가 바뀌었다」와 구별되지 않습니다.

**옮기지 복사하지 않습니다.** 두 자리에 같은 뜻의 칸을 두면 어느 쪽이 맞는지 화면만 봐서는
알 수 없습니다 — 콘솔이 계약 쪽을 쓰기 시작하면 고객 쪽은 그날부터 낡은 값입니다.

옛 값은 그 고객의 **모든 계약**에 같이 들어갑니다. 운영에서 이 칸이 채워진 고객은 한 곳
(2094번, 계약 1건)뿐이라 실제로는 한 줄이 그대로 옮겨 갑니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_ADD = (("contact_name", "VARCHAR(120)"), ("contact_info", "VARCHAR(255)"))


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "client_contracts" not in tables:
        logger.info("0103: client_contracts 없음, 건너뜁니다.")
        return

    have = {c["name"] for c in inspector.get_columns("client_contracts")}
    with engine.begin() as conn:
        for column, ddl in _ADD:
            if column not in have:
                conn.execute(text(f"ALTER TABLE client_contracts ADD COLUMN {column} {ddl}"))

        if "clients" not in tables:
            return
        old = {c["name"] for c in inspector.get_columns("clients")}
        for column, _ddl in _ADD:
            if column not in old:
                continue
            moved = conn.execute(
                text(
                    f"UPDATE client_contracts SET {column} = ("
                    f"  SELECT c.{column} FROM clients c"
                    f"  WHERE c.client_id = client_contracts.client_id"
                    f") WHERE {column} IS NULL"
                )
            ).rowcount
            try:
                conn.execute(text(f"ALTER TABLE clients DROP COLUMN {column}"))
            except Exception:
                # 아주 오래된 SQLite 는 DROP COLUMN 이 없습니다. 개발용 파일 DB 에서만 나올
                # 수 있고, 칸이 남아 있어도 이제 아무도 안 읽습니다.
                logger.warning("0103: clients.%s 를 못 지웠습니다 (무시).", column, exc_info=True)
            logger.info("0103: %s — 계약 %s행으로 옮기고 고객 열을 지웠습니다.", column, moved)
