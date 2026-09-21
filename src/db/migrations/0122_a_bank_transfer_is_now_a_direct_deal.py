"""결제 수단 「계좌이체」를 「직접거래」로 옮깁니다 (2026-09-21 운영자 지시).

**이름만 바꾸는 일이 아닙니다.** `client_contracts.payment_method` 는 고르개의 라벨이 아니라
**그 한글 글자를 그대로 저장하는 칸**입니다(`String(32)`, 검증도 CHECK 도 없습니다). 그래서
`won.PAYMENT_METHODS` 목록만 고치면 옛 글자를 든 행이 이렇게 됩니다:

- 계약 편집을 열면 `<select>` 에 「계좌이체」 항목이 없으므로 브라우저가 **첫 항목(Stripe)**
  을 그립니다. 그런데 초안 상태는 여전히 「계좌이체」라, 다른 칸만 고쳐 저장하면 **화면에
  적힌 것과 저장되는 것이 갈립니다.**
- 워크북 「결제 수단」 열의 드롭다운(`scripts/build_won_sheets.py` 의 CHOICES)에도 없는 말이
  되어, 그 행이 영업팀의 어느 필터에도 안 걸립니다. 드롭다운은 `strict: False` 라 **거절되지
  않고** 경고 삼각형만 뜹니다 — 반쪽만 바꾸면 아무것도 눈에 띄게 깨지지 않고 조용히 빠집니다.

그래서 목록과 같은 릴리스에 행을 옮깁니다. 코드에서 이 값을 비교하는 곳은 `ui_api._is_stripe`
한 줄이고 그것은 `"stripe"` 만 보므로, 옮기지 않아도 **아무것도 터지지 않습니다** — 그게
위험한 쪽입니다.

**`contract_records.payment_method` 는 건드리지 않습니다.** 레거시 리드 히스토리 계약 폼의
그 칸은 영문 키(`bank_transfer`)를 저장하고 시트 경계에서만 한글로 바뀝니다. 한글로 옮기면
그 폼의 `<select>` 매칭과 `sheet_sync._order_record` 의 조회가 동시에 깨집니다.

살아 있는 워크북의 드롭다운 목록은 이 이관이 못 바꿉니다 — 값이 규칙 안에 들어 있어서
`python -m scripts.build_won_sheets --refresh-derived` 를 한 번 돌려야 합니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_OLD = "계좌이체"
_NEW = "직접거래"


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "client_contracts" not in set(inspector.get_table_names()):
        logger.info("0122: client_contracts 없음, 건너뜁니다.")
        return
    if "payment_method" not in {c["name"] for c in inspector.get_columns("client_contracts")}:
        logger.info("0122: payment_method 칸이 없어 건너뜁니다.")
        return
    with engine.begin() as conn:
        moved = conn.execute(
            text(
                "UPDATE client_contracts SET payment_method = :new "
                "WHERE payment_method = :old"
            ),
            {"new": _NEW, "old": _OLD},
        ).rowcount
    # 운영 DB 는 개발망에서 조회할 수 없습니다. 몇 행이었는지를 아는 길은 이 로그뿐입니다.
    logger.info("0122: 결제 수단 %s → %s, %s행.", _OLD, _NEW, moved)
