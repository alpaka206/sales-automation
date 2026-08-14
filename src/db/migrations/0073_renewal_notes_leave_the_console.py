"""「갱신 · 비고」는 콘솔이 관리하지 않습니다 — 패널도, 세 열도 지웁니다.

운영자 지시(2026-08-14): 수주 고객 상세의 「갱신 · 비고」 섹션을 아예 지운다. 그래서 그
패널만 쓰던 세 열(``renewal_plan`` · ``stop_reason`` · ``memo``)은 남겨 둘 이유가 없습니다.
읽는 화면이 없는 열은 **아무도 안 보는데 계속 동기화되는 값**이 되고, 다음 사람이 열어 보고
"이건 왜 비어 있지" 를 확인하러 갑니다(0072 가 클레임 테이블에 대해 한 말과 같습니다).

같이 없어진 것:

- 화면: 상세 6번 섹션 전체(`CareSection` · `ContractNotes`). 뒤 번호를 당겼습니다 — 매출
  관리가 6, 소통 히스토리가 7 입니다. 앵커로 들어오는 링크는 4·5번뿐이라(`WonCustomers.tsx`
  의 sec-credit · sec-pay) 어긋나는 자리가 없습니다.
- 라우트: ``/won-customers/contracts/{id}`` 는 그대로지만 세 이름을 안 받습니다
  (``_CONTRACT_FIELDS``). CSV 의 「갱신 계획」 열도 빠집니다.
- 선택지: ``won.RENEWAL_PLANS``. 남은 사용처가 워크북 드롭다운뿐이고 그 목록은
  ``scripts/build_won_sheets.py`` 의 ``CHOICES`` 가 따로 들고 있습니다.
- 동기화: 「계약 및 결제 정보」 탭 W·X·Y 로 내보내고 읽어 오던 경로.
  **시트의 열 자체는 그대로 둡니다** — 시트는 운영자의 것이고, 콘솔이 안 건드리므로 손으로
  적는 자리가 됩니다(AM「담당」 열과 같은 처지). 그래서 ``owned`` 에서도 뺐습니다: 남겨
  두면 콘솔이 지운 고객의 행에서 그 세 칸까지 같이 비웁니다.

**행에 값이 있으면 사라집니다.** 되돌릴 수 없습니다 — 다만 같은 값이 워크북 W·X·Y 에 이미
있고, 이제 그쪽이 원본입니다. 그래도 지우기 전에 몇 건이었는지 로그에 남깁니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_GONE = ("renewal_plan", "stop_reason", "memo")


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "client_contracts" not in set(inspector.get_table_names()):
        logger.info("0073: client_contracts 가 없어 건너뜁니다.")
        return
    existing = {c["name"] for c in inspector.get_columns("client_contracts")}
    for column in _GONE:
        if column not in existing:
            continue
        with engine.begin() as conn:
            filled = conn.execute(
                text(
                    f"SELECT COUNT(*) FROM client_contracts "
                    f"WHERE {column} IS NOT NULL AND {column} <> ''"
                )
            ).scalar_one()
            logger.info("0073: client_contracts.%s 를 지웁니다 — 값이 있던 행 %s", column, filled)
            try:
                conn.execute(text(f"ALTER TABLE client_contracts DROP COLUMN {column}"))
            except Exception:
                # 아주 오래된 SQLite. 읽는 곳이 없으므로 남아 있어도 무해합니다.
                logger.warning("0073: client_contracts.%s 를 지우지 못했습니다.", column)
    logger.info("0073: 갱신 계획·사용 중단 이유·비고는 워크북에만 남습니다.")
