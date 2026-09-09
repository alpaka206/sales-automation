"""계약 날짜와 똑같이 굳어 있던 플랜 날짜를 비웁니다 (2026-09-09 운영자 보고).

**증상**: 수주 고객에서 계약 날짜를 고쳐도 MRR 이 안 바뀐다. 「월간 MRR (공급가 기준)」만
움직이고 나머지도, 밖의 목록도 그대로다.

**원인**: 저장 경로가 `plan_starts_on = plan_starts_on or starts_on` 으로 「비우면 계약
기간과 같다」를 **값으로 굳혔습니다.** MRR 은 플랜 기간으로 나누므로(`won.plan_months`),
한 번 굳고 나면 계약 날짜를 아무리 고쳐도 분모가 안 움직입니다. 그 사이 화면의 「공급가
기준」만 계약 개월수로 직접 나누고 있어서 그것만 따라 움직였고, 두 숫자가 갈린 덕에
운영자가 알아챘습니다.

파생값을 저장하면 원본이 바뀔 때 조용히 어긋난다 — 이 저장소가 이미 두 번 겪은
자리입니다(`customer_profiles.qualification` 0104, 고객 종류 0065).

**계약 날짜와 똑같은 행만 비웁니다.** 그 행들은 「굳은 기본값」이라 비워도 읽는 값이 한
글자도 안 바뀝니다(`plan_period` 가 계약 날짜로 떨어집니다) — 달라지는 것은 **앞으로
계약 날짜를 고치면 따라온다**는 것뿐입니다. 다른 행은 운영자가 일부러 다르게 적은
것이므로 손대지 않습니다.

한쪽만 같은 행도 그쪽 칸만 비웁니다 — 시작일은 같고 만료일만 늘려 적은 계약이 실제로
있을 수 있고, 그때 시작일은 여전히 계약을 따라야 합니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "client_contracts" not in set(inspector.get_table_names()):
        logger.info("0117: client_contracts 없음, 건너뜁니다.")
        return
    columns = {c["name"] for c in inspector.get_columns("client_contracts")}
    if not {"plan_starts_on", "plan_ends_on", "starts_on", "ends_on"} <= columns:
        logger.info("0117: 플랜/계약 날짜 칸이 없어 건너뜁니다.")
        return
    with engine.begin() as conn:
        started = conn.execute(text(
            "UPDATE client_contracts SET plan_starts_on = NULL "
            "WHERE plan_starts_on IS NOT NULL AND plan_starts_on = starts_on"
        )).rowcount
        ended = conn.execute(text(
            "UPDATE client_contracts SET plan_ends_on = NULL "
            "WHERE plan_ends_on IS NOT NULL AND plan_ends_on = ends_on"
        )).rowcount
    logger.info("0117: 플랜 시작일 %s행, 만료일 %s행을 계약 날짜 기본값으로 되돌렸습니다.",
                started, ended)
