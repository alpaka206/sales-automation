"""이관 0123 이 그대로 베낀 계약금액에 VAT 를 더해 되돌립니다 (2026-09-21).

**무슨 일이 있었나.** 0123 의 규칙 ③ 은 「`amount_incl_vat` 가 비어 있고 `amount_excl_vat` 만
있는 행은 그 값을 올려 쓴다」인데, 그 값을 **그대로** 베꼈습니다. 그런 행이 없을 거라고 적어
두었지만(「그렇게 쓰는 경로가 없어 아마 0건」) 운영에 **7건** 있었고, 그대로 배포됐습니다.

그 7건의 총액이 10% 내려앉았습니다. 옛 `won.total_amount` 는 그 행에서 `공급가 × 1.1` 을
돌려주고 있었고 — 두 칸이 다 채워진 행과 달리 이 행들은 포함 값이 **계산값**이었습니다 —
화면의 「총 계약금액」과 워크북 계약 탭 L열이 그 값을 보여 주고 있었습니다. 실측으로 확인한
한 건: `2102-1` 은 시트에 L 1,722,600 · M 1,566,000 이었고, 0123 뒤 계약금액이 1,566,000 이
됐습니다. 1,566,000 × 1.1 = 1,722,600 — 시트가 보여 주던 그 값입니다.

**대상을 어떻게 찾나.** `amount_excl_vat` 는 0123 이 지웠으므로 「원래 공급가만 있던 행」을
그 칸으로는 못 가립니다. 대신 0123 이 같은 트랜잭션에서 남긴 **계약비고**를 씁니다:
「실제 분당단가 {단가} {통화}/분 (VAT 미포함 기준)」. 그 단가는 **VAT 미포함 기준**이므로,

- 그대로 베껴진 행: 지금 `계약금액 ÷ (크레딧 ÷ 60)` == 비고의 단가
- 포함 값이 원래 있던 행: 지금 단가 == 비고의 단가 × 1.1

즉 **산수가 대상임을 증명하는 행만** 손댑니다. 그리고 고친 뒤에는 그 등식이 깨지므로 다시
돌려도 아무 일이 없습니다 — Client ID 를 코드에 박는 것보다 안전하고, 어느 DB 에서 돌려도
같은 답을 냅니다.

**분납 회차는 그대로 맞습니다.** 그 회차들은 옛 총액(= 공급가 × 1.1)으로 깔렸고 이 이관이
총액을 그 값으로 되돌리므로, 0123 뒤 잠깐 어긋났던 수금율이 제자리로 갑니다.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

VAT_RATE = Decimal("0.1")
CREDITS_PER_MINUTE = 60
# 0123 이 적은 줄. 단가는 천 단위 쉼표가 있고 소수점은 있을 수도 없을 수도 있습니다.
_NOTE = re.compile(r"실제 분당단가\s+([0-9,]+(?:\.[0-9]+)?)\s+\S*/분 \(VAT 미포함 기준\)")


def _rate_in_note(note: str | None) -> Decimal | None:
    match = _NOTE.search(note or "")
    if match is None:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "client_contracts" not in set(inspector.get_table_names()):
        logger.info("0126: client_contracts 없음, 건너뜁니다.")
        return
    columns = {c["name"] for c in inspector.get_columns("client_contracts")}
    if not {"amount_incl_vat", "credits", "note"} <= columns:
        logger.info("0126: 필요한 칸이 없어 건너뜁니다.")
        return

    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT client_id, seq, credits, amount_incl_vat, note FROM client_contracts "
            "WHERE note LIKE '%실제 분당단가%' "
            "AND amount_incl_vat IS NOT NULL AND credits IS NOT NULL AND credits > 0"
        )).mappings().all()

        fixed = 0
        for row in rows:
            noted = _rate_in_note(row["note"])
            if noted is None:
                continue
            try:
                amount = Decimal(str(row["amount_incl_vat"]))
            except InvalidOperation:
                continue
            minutes = Decimal(row["credits"]) / CREDITS_PER_MINUTE
            now = (amount / minutes).quantize(Decimal("0.01"))
            # 지금 단가가 **VAT 미포함 기준 단가와 같다** = 0123 이 공급가를 그대로 베꼈다.
            # 포함 값이 원래 있던 행은 그 단가의 1.1배라 여기서 걸리지 않습니다.
            if abs(now - noted) > Decimal("0.01"):
                continue
            restored = (amount * (1 + VAT_RATE)).quantize(Decimal("0.01"))
            conn.execute(
                text("UPDATE client_contracts SET amount_incl_vat = CAST(:amount AS NUMERIC) "
                     "WHERE client_id = :cid AND seq = :seq"),
                {"amount": str(restored), "cid": row["client_id"], "seq": row["seq"]},
            )
            fixed += 1
            # 금액은 안 적습니다 — `/logs` 의 스크러버가 9자리 이상 숫자를 전화번호로 지웁니다.
            logger.info("0126: %s-%s 계약금액에 VAT 를 더해 되돌렸습니다.",
                        row["client_id"], row["seq"])

    logger.info("0126: 0123 이 그대로 베낀 계약금액 %s행을 되돌렸습니다 "
                "(비고에 VAT 미포함 단가가 적힌 행 %s건 중).", fixed, len(rows))
