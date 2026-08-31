"""계약마다 그 시점 환율을 박아 둡니다 — 옛 행 백필 (2026-08-31 운영자 지시).

예상 MRR 카드는 계약을 **원화와 USD 양쪽으로** 보여 줍니다. 환산에 쓰는 값은 그 계약에
박힌 ``fx_rate`` 이고, 없으면 **오늘 고시가**로 떨어집니다(`ui_api._contract_rate`). 그래서
환율이 비어 있는 계약은 **어제 본 숫자와 오늘 본 숫자가 다릅니다** — 마감한 달의 매출이
오늘 환율에 따라 움직입니다.

운영 34건 중 **33건이 비어 있었습니다** (USD 18 · KRW 15). USD 계약만 채우던 코드에도
날짜가 없으면 건너뛰는 갈래가 있었고, 원화 계약은 「환산할 것이 없다」며 아예 건너뛰었습니다
— 한쪽 방향만 본 이야기였습니다: 원화 계약도 USD 로 환산되어 보입니다.

**넣는 값은 계약일 고시가입니다.** 같은 날짜는 한 번만 조회합니다. 그 날짜를 못 가져오면
오늘 고시가로 떨어지고, 그것도 실패하면 그 행은 비워 둡니다 — 계약을 한 번 저장하면
`_fill_contract_fx` 가 같은 규칙으로 다시 채웁니다.

**절대 안 터집니다.** 환율 API 한 번 실패했다고 배포가 멈추면 안 됩니다. 못 채운 행은
수를 로그에 남기고 넘어갑니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, Numeric, bindparam, inspect, text

logger = logging.getLogger(__name__)


def _rate_for(day: str | None, cache: dict, today_rate) -> tuple | None:
    """그 날짜의 (환율, 고시일). 같은 날짜는 한 번만 조회합니다."""
    from ...integrations import fx

    if not day:
        return today_rate
    if day in cache:
        return cache[day]
    try:
        found = fx.usd_krw_on(day)
    except Exception:
        found = None
    cache[day] = (found[0], found[1]) if found else today_rate
    return cache[day]


def up(engine: Engine) -> None:
    if "client_contracts" not in set(inspect(engine).get_table_names()):
        logger.info("0102: client_contracts 없음, 건너뜁니다.")
        return

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT client_id, seq, starts_on, first_payment_on FROM client_contracts "
                "WHERE fx_rate IS NULL"
            )
        ).fetchall()
    if not rows:
        logger.info("0102: 환율이 빈 계약이 없습니다.")
        return

    try:
        from ...integrations import fx

        today = fx.usd_krw_today()
    except Exception:
        today = None
    today_rate = (today[0], today[1]) if today else None

    cache: dict[str, tuple | None] = {}
    filled = 0
    with engine.begin() as conn:
        for row in rows:
            rate = _rate_for(row.starts_on or row.first_payment_on, cache, today_rate)
            if not rate:
                continue
            conn.execute(
                # ``Decimal`` 은 타입을 적어 줘야 합니다 — 안 적으면 SQLite 가 못 바인딩하고
                # (raw SQL 이라 열 타입을 안 봅니다), Postgres 는 text→numeric 캐스트를
                # 거부합니다. 계약 금액과 같은 정밀도(12,4)로 넘깁니다.
                text(
                    "UPDATE client_contracts SET fx_rate = :rate, fx_on = :on "
                    "WHERE client_id = :client AND seq = :seq"
                ).bindparams(bindparam("rate", type_=Numeric(12, 4))),
                {"rate": rate[0], "on": rate[1], "client": row.client_id, "seq": row.seq},
            )
            filled += 1
    logger.info(
        "0102: 계약 %d건 중 %d건에 환율을 박았습니다 (조회한 날짜 %d개). 못 채운 %d건은 "
        "저장할 때 `_fill_contract_fx` 가 다시 채웁니다.",
        len(rows), filled, len(cache), len(rows) - filled,
    )
