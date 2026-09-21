"""`clients.industry`(산업 분야) 를 지웁니다 (2026-09-21 운영자 지시).

    고객 기본 정보에서 산업 분야도 삭제 (입력도 이전 데이터도 사라져도 되니깐 노출될 필요 x)

「읽는 코드가 있는 칸만 남긴다」(0101)의 연장이지만 성격이 다릅니다 — 이 칸은 파생값이
아니라 **정보**라서, 지우면 돌아오지 않습니다. 운영자가 그걸 알고 지우라고 했습니다.

**워크북 「고객 기본 정보」 E열은 남깁니다.** 그 탭의 열 순서가 곧 좌표입니다
(`won_sheets` 의 owned 글자, `sheet_to_db` 의 `cell(r, "F")`, `google_sheets.
_REGISTRY_COLUMNS`, `build_won_sheets` 의 `("I", 5)`) — 열 하나를 지우면 F~J 가 한 칸씩
밀려 들어가고 **예외는 안 납니다.** 그래서 콘솔이 그 칸을 안 쓰게만 했습니다(owned 에서
E 를 뺐습니다): 운영자가 직접 적는 칸이 됐고, 갱신 계획(0073)이 지나간 그 자리입니다.

그 열을 조회해 가는 곳이 하나 있습니다 — Inbound DB 탭의 「기업 종류」
(`=VLOOKUP(..., 5, FALSE)`). 그쪽은 계속 채워집니다: 새 인바운드 회사의 행을 만드는
`google_sheets._ensure_registry_row` 가 Gemini 기업 종류 분류 결과를 E 에 쓰고, 그 경로는
`Client.industry` 를 보지 않습니다. 콘솔에서만 만든 고객(GTM Outbound·Interactive·AX)은
E 가 빈 채로 남는데, 그 고객들은 Inbound DB 에 행이 없어 조회가 비는 일도 없습니다.

**`customer_profiles.industry`(「산업군」)와 `domain_profiles.industry` 는 다른 칸이고
그대로입니다.** 앞엣것은 리드 히스토리 고객 상세가 그리는 값이고 허브스팟 연락처에서
동기화됩니다(`contact_sync.FIELDS`) — 같이 지우면 그 화면이 빕니다.

되살리려면 **어느 화면이 그 값을 읽는지부터 정하세요**(0095 · 0104 · 0116 과 같은 규칙).
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "clients" not in set(inspector.get_table_names()):
        logger.info("0124: clients 없음, 건너뜁니다.")
        return
    if "industry" not in {c["name"] for c in inspector.get_columns("clients")}:
        logger.info("0124: clients.industry 가 이미 없습니다.")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE clients DROP COLUMN industry"))
    logger.info("0124: clients.industry 를 지웠습니다.")
