"""`client_contracts.vat_applicable` 을 지웁니다 — 공급가가 없어졌으니 (2026-09-22 운영자 지시).

    공급가 아예 제외 해도 될거같아.
    vat 해당 여부도 이제 안쓸테니 삭제해도 돼

**무슨 뜻인가.** 2026-09-21(이관 0123)부터 계약 금액은 `amount_incl_vat` 한 칸이고 그 값이
VAT 포함 총액이었습니다. 공급가는 저장하지 않고 **총액 ÷ 1.1** 로 되짚었고(`won.supply_amount`),
`vat_applicable` 이 남아 있던 이유는 그 함수 하나 — 「공급가라는 것이 있는 계약인가」를 아는
곳이 그 칸뿐이었습니다. 공급가를 아예 안 보여 주기로 했으니 그 물음도 없어졌고, 답을 들고 있던
칸도 같이 나갑니다.

남는 것은 **총액 하나**입니다. 분당 단가는 그대로 총액 ÷ (크레딧 ÷ 60)(`won.unit_price`),
월간 매출도 총액 ÷ 계약 개월수 — 이 이관은 금액을 한 자리도 안 움직입니다. 화면·CSV·API 에서
「공급가 (VAT 제외)」·「VAT 해당 여부」·「월간 MRR (공급가 기준)」이 빠집니다.

**워크북 계약 탭 M열(공급가)은 자리를 지킵니다.** 그 탭의 열 순서가 곧 좌표라(`won_sheets` 의
owned 글자 · `sheet_to_db` · `build_won_sheets` 의 수식) 열 하나를 지우면 N~AM 이 한 칸씩 밀려
들어가고 **예외는 안 납니다.** 그래서 콘솔이 그 칸에 빈칸을 씁니다 — I열(계약서 유형, 0125)·
N·P 와 같은 규칙이고, owned 에 남겨 두는 이유도 같습니다: 빼면 `plan_tab` 이 비운 행을 다음
계약에 다시 쓸 때 지워진 계약의 옛 공급가가 남의 행에 얹힙니다.

되살리려면 **어느 화면이 그 값을 읽는지부터 정하세요**(0095 · 0104 · 0116 · 0124 와 같은 규칙).
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "client_contracts" not in set(inspector.get_table_names()):
        logger.info("0127: client_contracts 없음, 건너뜁니다.")
        return
    if "vat_applicable" not in {c["name"] for c in inspector.get_columns("client_contracts")}:
        logger.info("0127: client_contracts.vat_applicable 이 이미 없습니다.")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE client_contracts DROP COLUMN vat_applicable"))
    logger.info("0127: client_contracts.vat_applicable 을 지웠습니다.")
