"""세 개의 칸: 결말의 이유, 계약서에 적힌 금액의 기준, 클레임을 보낸 사람.

셋 다 **없어서 화면이 거짓말을 하던 값**입니다.

1. ``conversations.deal_detail`` — Won 과 Lost 는 결말이지 이유가 아닙니다. 왜 이겼는지
   (PoC / Contract / Renewal)와 왜 졌는지(가격 · 다른 플랜 · 경쟁사 · 기능 · 결정 없음 ·
   연락두절)를 적을 자리가 없어서, 이번 달에 왜 졌는지를 알려면 티켓을 하나씩 열어야
   했습니다. **열 하나입니다** — 한 문의가 동시에 이기고 지지 않으므로, 어느 목록의
   값인지는 그때의 단계가 정합니다(``customer_ops.DEAL_DETAILS``).

2. ``client_contracts.vat_included`` — 원화 계약은 공급가만 받고 총액을 +10% 로 계산해
   왔습니다. 그런데 계약서가 늘 공급가로 적히지는 않습니다: 총액으로 적힌 계약을 그
   칸에 넣으면 분당 단가가 10% 낮게 나오고, 화면 어디에도 그게 보이지 않습니다. 이 값이
   **그 계약의 금액이 어느 쪽인지** 를 행에 박아 둡니다. 기존 행은 전부 ``false`` 이고,
   그것이 지금까지의 동작 그대로입니다 — 값이 바뀌는 계약은 하나도 없습니다.
   USD 계약은 이 값을 보지 않습니다(부가세가 없어 총액만 받습니다).

3. ``contract_claims.contact_info`` — 클레임이 들어온 연락처. 등록된 담당자와 다를 수
   있습니다(실무자가 항의 메일을 보내고, 답은 그 사람에게 갑니다). 지금까지는 적을 곳이
   없어 「클레임 종류」 칸에 섞여 들어갔습니다.

파이프라인 단계 이름(Meeting Link Sent → Qualified, Closed → Not a Fit)은 여기 없습니다.
HubSpot 에서 **이름만** 바뀌었고 stage id 도 로컬 키도 그대로라, 옮길 행이 없습니다 —
바뀐 것은 ``customer_ops.PIPELINE_STAGES`` 의 표시 이름 한 곳뿐입니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

def up(engine: Engine) -> None:
    # BOOLEAN 의 거짓 리터럴만 엔진마다 다릅니다(0003 과 같은 처리). 기본값을 붙여 추가하면
    # 기존 행이 그 자리에서 채워지므로 따로 UPDATE 할 것이 없습니다.
    false_literal = "0" if engine.dialect.name == "sqlite" else "false"
    columns: tuple[tuple[str, str, str], ...] = (
        ("conversations", "deal_detail", "VARCHAR(32)"),
        ("client_contracts", "vat_included", f"BOOLEAN NOT NULL DEFAULT {false_literal}"),
        ("contract_claims", "contact_info", "VARCHAR(255)"),
    )

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in columns:
            if table not in tables:
                logger.info("0071: %s 테이블이 없어 건너뜁니다", table)
                continue
            if column in {col["name"] for col in insp.get_columns(table)}:
                logger.info("0071: %s.%s 이미 있습니다", table, column)
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            logger.info("0071: %s.%s 추가", table, column)
