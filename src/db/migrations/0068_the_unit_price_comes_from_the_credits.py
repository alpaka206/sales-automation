"""분당 단가를 저장하지 않습니다 — 금액과 크레딧에서 나옵니다.

방향이 반대였습니다. 단가를 받아 크레딧을 계산했고(크레딧 = 공급가 ÷ 단가 × 60), 통화가
다르면 그때 쓴 환율까지 계약 행에 박아야 했습니다. 그런데 계약서에 적히는 것은 **금액과
크레딧**이고 단가는 그 둘에서 나오는 값입니다. 받는 쪽이 뒤집혀 있으니 반올림한 단가로
계산한 크레딧이 계약서의 크레딧과 어긋났습니다.

이제 `won.unit_price` 가 계산합니다: 기준 금액 ÷ (계약 크레딧 ÷ 60). 소수점은 남깁니다.
운영자 시트의 실제 계약으로 검산하면 1,566,000 ÷ (64,800 ÷ 60) = 1,450원/분 입니다.

기준 금액은 통화가 정합니다:

- 원화 계약 → 공급가(VAT 제외). 총 계약금액은 여기에 10% 를 더해 **계산**하므로 입력 칸이
  없습니다.
- 그 외 통화 → 총 계약금액. 해외 계약에는 그 부가세가 없어 총액이 곧 대금이고, 공급가
  칸이 없습니다.

그래서 세 열을 지웁니다: ``unit_price`` (계산값), ``unit_currency`` · ``unit_fx_rate``
(단가 통화가 없어졌으니 환산도 없습니다). 남겨 두면 다음 사람이 그것을 읽고, 계산값과
저장값이 갈라진 계약이 생깁니다.

시트도 같이 바뀝니다: O(분당 단가)는 계산값이 나가고, N(단가 통화)·P(적용 환율)은 비웁니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_GONE = ("unit_price", "unit_currency", "unit_fx_rate")


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "client_contracts" not in set(inspector.get_table_names()):
        logger.info("0068: client_contracts 가 없어 건너뜁니다.")
        return
    existing = {c["name"] for c in inspector.get_columns("client_contracts")}
    for column in _GONE:
        if column not in existing:
            continue
        with engine.begin() as conn:
            try:
                conn.execute(text(f"ALTER TABLE client_contracts DROP COLUMN {column}"))
            except Exception:
                # 아주 오래된 SQLite. 읽는 곳이 없으므로 남아 있어도 무해합니다.
                logger.warning("0068: client_contracts.%s 를 지우지 못했습니다.", column)
    logger.info("0068: 분당 단가는 이제 금액과 크레딧에서 나옵니다.")
