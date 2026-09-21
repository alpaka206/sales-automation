"""`client_contracts.doc_types`(계약서 유형) 를 지웁니다 (2026-09-21 운영자 지시: 「계약서
유형도 아예 삭제」).

읽어 쓰는 코드가 없는 순수 기록용 칸이었습니다 — 폼의 체크박스 묶음, 상세의 한 줄, CSV 한
열, 워크북 계약 탭 I열. 파생시키는 값도, 이 값으로 갈라지는 분기도 하나도 없습니다.

**워크북 계약 탭 I열은 남깁니다.** `won_sheets._contract_row` 와 `sheet_to_db` 와
`build_won_sheets` 가 전부 **열 문자**로 접근하므로, 열 하나를 지우면 J(계약 크레딧)부터
AM(담당)까지 한 칸씩 밀려 들어가고 예외는 안 납니다 — 화면에는 안 보이고 영업팀 시트에서만
보입니다. 대신 콘솔이 그 칸을 **빈칸으로 내보냅니다**(`"I": ""`, owned 에는 남깁니다):

- 콘솔이 아는 계약의 옛 글자는 다음 동기화에서 지워집니다. DB 값이 사라지는데 시트에만
  남으면 그 칸이 「콘솔이 더는 유지할 수 없는 기록」이 되어, 언젠가 사실과 갈립니다.
- owned 에서 I 를 빼면 안 됩니다: `plan_tab` 이 비운 행을 다음 계약에 다시 쓰므로
  (`free`), 지워진 계약의 계약서 유형 글자가 **남의 계약 행**에 얹힙니다. N·P 를 `""` 로
  비우는 이유가 정확히 그것입니다.

`0065_won_customers.py` 의 `doc_types {json}` 는 손대지 않습니다 — 옛 이관은 그때의 모양을
세우고 새 이관이 그때처럼 지웁니다(0019 → 0100 선례). 새 DB 는 0001 의 `create_all` 이 지금
모델로 표를 만들어 열이 아예 없고, 이 이관은 조용히 넘어갑니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "client_contracts" not in set(inspector.get_table_names()):
        logger.info("0125: client_contracts 없음, 건너뜁니다.")
        return
    if "doc_types" not in {c["name"] for c in inspector.get_columns("client_contracts")}:
        logger.info("0125: doc_types 가 이미 없습니다.")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE client_contracts DROP COLUMN doc_types"))
    logger.info("0125: client_contracts.doc_types 를 지웠습니다.")
