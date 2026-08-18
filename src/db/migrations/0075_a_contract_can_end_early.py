"""계약은 만료 전에 끝날 수 있고, 부가세는 통화가 아니라 고객이 정한다.

세 칸을 더합니다.

``client_contracts.terminated_on`` — **중도 해지일.** 플랜은 만료일과 이 날짜 중 **빠른
쪽**에서 끝납니다. 지금까지 계약이 끝나는 길은 만료일 하나뿐이라, 중간에 그만둔 고객도
남은 달의 MRR 을 계속 얹고 있었습니다.

``client_contracts.credits_used`` — **크레딧 사용량.** 예상 환불 금액이 여기서 나옵니다
(남은 크레딧 비율 × 계약금액). 자동으로 채우지 않습니다: 제품 쪽에서 이 값을 가져오는
경로가 아직 없고, 없는 값을 0 으로 두면 「하나도 안 썼으니 전액 환불」이 되어 해지월 매출이
통째로 음수가 됩니다. 운영자가 적을 때까지는 **비어 있는 것이 맞습니다.**

``client_contracts.vat_applicable`` — **부가세 해당 여부.** 지금까지는 통화가 정했습니다
(``won.is_krw``): 원화면 부가세가 있고 그 외에는 없다고. 실제 기준은 **국내 법인 고객인가**
이고, 통화와 늘 같이 가지는 않습니다. 운영자가 계약마다 고릅니다.

**기존 행은 손대지 않습니다.** 이 칸은 NULL 을 허용하고, NULL 은 「아직 안 고름」이라
`won.vat_applicable` 이 예전 규칙대로 통화로 추정합니다 — 원화면 해당, 그 외는 미해당.
그래서 이 이관 하나로 과거 계약의 총액이나 분당 단가가 움직이지 않습니다. 값을 채우는
UPDATE 를 여기서 돌리지 않는 이유이기도 합니다: 수백 행을 지금 굳혀 두면, 그중 예외였던
계약(원화인데 부가세가 없는 건)을 나중에 찾아낼 방법이 없습니다.

``client_contracts.fx_rate`` · ``fx_on`` — **그 계약에 적용할 환율과 그 기준 날짜.** 지금까지
환율은 결제 회차(``contract_payments``)에만 있었고, 대시보드의 예상 MRR 은 **오늘 고시가**로
환산했습니다. 그러면 같은 계약의 지난달 매출이 이번 달에 달라 보입니다. 계약에 박아 두면
그 계약의 매출은 언제 봐도 같은 숫자입니다. 비워 두면 저장할 때 계약일 기준 고시가를
조회해 채웁니다(``fx.usd_krw_on``) — 조회가 실패하면 비어 있고, 그때는 예전처럼 카드가
오늘 고시가로 환산합니다.

``vat_included`` 는 이름을 그대로 둡니다. 뜻도 그대로입니다 — **분당 단가의 기준이 VAT
포함 금액인가.** 새 화면에서 그 값을 고르는 자리가 「공급가 선택」이 되었을 뿐입니다.
이름을 바꾸는 이관은 살아 있는 금액 열을 건드리는 일이라, 뜻이 안 바뀌는데 할 이유가
없습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_COLUMNS = (
    ("terminated_on", "VARCHAR(10)"),
    ("credits_used", "INTEGER"),
    ("vat_applicable", "BOOLEAN"),
    ("fx_rate", "NUMERIC(12, 4)"),
    ("fx_on", "VARCHAR(10)"),
)


def up(engine: Engine) -> None:
    insp = inspect(engine)
    if "client_contracts" not in set(insp.get_table_names()):
        logger.info("0075: client_contracts 테이블이 없어 건너뜁니다")
        return
    existing = {col["name"] for col in insp.get_columns("client_contracts")}
    with engine.begin() as conn:
        for column, ddl in _COLUMNS:
            if column in existing:
                logger.info("0075: client_contracts.%s 이미 있습니다", column)
                continue
            conn.execute(text(f"ALTER TABLE client_contracts ADD COLUMN {column} {ddl}"))
            logger.info("0075: client_contracts.%s 추가", column)
