"""클레임은 콘솔이 관리하지 않기로 했습니다 — 표도, 라우트도, 테이블도 지웁니다.

운영자 지시(2026-08-13): 고객 클레임은 콘솔 밖에서 관리한다. 그래서 남겨 둘 이유가 없습니다.
읽는 화면이 없는 테이블은 **아무도 안 보는데 계속 동기화되는 값**이 되고, 다음 사람이 열어
보고 "이건 왜 비어 있지" 를 확인하러 갑니다.

같이 없어진 것:

- 화면: 수주 고객 상세 6번 섹션의 클레임 표(섹션 자리는 「갱신 · 비고」로 남습니다 — 그
  패널은 계약의 값이지 클레임이 아니었습니다), 목록의 「미처리 클레임」 액션 보드.
- 라우트: ``/won-customers/contracts/{id}/claims`` 와 ``/won-customers/claims/{id}``(+delete).
- 파생값: ``won.open_claims`` · ``CLAIM_PROGRESS`` · CSV 의 「미처리 클레임」 열.
- 동기화: 워크북 「클레임 · 히스토리」 탭으로 내보내던 경로와 그 탭에서 읽어 오던 경로.
  **시트의 탭 자체는 그대로 둡니다** — 시트는 운영자의 것이고, 손으로 적는 자리가 됩니다
  (소통 히스토리 탭과 같은 처지). 콘솔이 안 건드리므로 지워지지도 않습니다.

**행이 있으면 사라집니다.** 되돌릴 수 없습니다. 그래서 지우기 전에 몇 줄이었는지 로그에
남깁니다 — 나중에 "그때 몇 건 있었나" 를 물을 곳이 이것뿐입니다.

0071 이 이 테이블에 ``contact_info`` 열을 더한 바로 다음 이관입니다. 되돌려 쓰지 않는
이유: 마이그레이션은 역사라 고쳐 쓰지 않습니다. 새 DB 에서는 0065 가 만들고 0071 이 열을
더한 뒤 여기서 지워집니다 — 결과는 같고, 그 사이에 무슨 일이 있었는지가 남습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_TABLE = "contract_claims"


def up(engine: Engine) -> None:
    insp = inspect(engine)
    if _TABLE not in set(insp.get_table_names()):
        logger.info("0072: %s 테이블이 없어 건너뜁니다", _TABLE)
        return
    with engine.begin() as conn:
        rows = conn.execute(text(f"SELECT COUNT(*) FROM {_TABLE}")).scalar_one()
        logger.info("0072: %s 을(를) 지웁니다 — 지금 %s 행", _TABLE, rows)
        conn.execute(text(f"DROP TABLE {_TABLE}"))
