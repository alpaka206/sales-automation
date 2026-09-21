"""계약 금액을 **VAT 포함 한 칸**으로 합칩니다 (2026-09-21 운영자 지시).

운영자 문장 그대로:

    계약금액은 모두 VAT 포함만 으로 바꿀거야
    기존에 vat 포함이였던 것들은 그대로 쓰면 되고
    포함, 미포함 둘다 써있던건 미포함만 남겨두면 되고
    미포함만 써있던건 그걸 vat 포함에 작성해두면 돼
    분당단가가 VAT미포함인 건들은 "계약비고"에 실제 분당단가 추가로 기재하도록 해줘

「둘 다 써 있던 건 어느 쪽을 남기나」를 되물었고 **「미포함 칸에 적었던 숫자」**로 답을
받았습니다. 그래서 이 이관은 그 숫자를 `amount_incl_vat` 로 옮깁니다 — 운영자가 계약서에서
옮겨 적은 그 숫자가 곧 계약금액이 됩니다.

**그 선택의 대가를 여기 적어 둡니다.** VAT 미포함 기준이던 계약은 총액이 10% 내려앉습니다
(11,000,000 → 10,000,000). 월간 MRR·예상 MRR 카드·월별 시리즈·CSV·리드 히스토리의 수주
요약이 전부 그만큼 내려가고, 월별 값은 읽을 때마다 다시 계산되므로 **이미 마감한 달의
숫자까지 움직입니다.** 운영자에게 그 표(총액 −10% · 월 MRR −10% · 예상 MRR 합계 −10%)를
보여 주고 받은 결정입니다. 되돌릴 자료는 아래 로그뿐입니다.

같이 **안** 움직이는 것 둘:

- **분납 회차 금액은 다시 깔지 않습니다.** 옛 총액으로 깔린 회차가 그대로 남아 그 계약의
  수금율이 100% 를 넘을 수 있습니다. 회차를 다시 까는 것은 운영자가 손으로 적은 입금일과
  완료 표시를 지우는 일이라, 기계가 조용히 할 일이 아닙니다.
- **워크북 「검증」 탭의 산정 크레딧 수식**은 `scripts/build_won_sheets.py` 에서 같이
  고쳤습니다(M ÷ O → L ÷ O). 공급가가 총액 ÷ 1.1 이 되면서 그 열이 계약 크레딧 ÷ 1.1 로
  나와 **모든 행**이 「크레딧 산정 불일치」로 찍히기 때문입니다 — 전부 빨개지면 그 열이
  하던 일(진짜 어긋난 행을 눈에 걸리게 하는 것)이 죽습니다. 살아 있는 워크북에는
  `python -m scripts.build_won_sheets --refresh-derived` 를 한 번 돌려야 반영됩니다.

**계약비고에 분당단가를 적는 이유.** 금액 칸이 하나가 되면 「이 계약의 단가는 VAT 미포함
기준이다」를 아는 칸이 없어집니다(`vat_included` 가 그 사실을 들고 있었습니다). 그 값을 다시
계산할 방법도 없어지므로, **열을 지우기 전에** 한 줄로 남깁니다. 덧붙이기만 하고(운영자
자유 텍스트입니다) 같은 줄이 이미 있으면 건너뜁니다.

`vat_applicable` 은 **남깁니다** — 「공급가라는 것이 있는 계약인가」를 아는 곳이 그 칸뿐이고,
같이 지우면 해외 계약의 공급가 칸이 0 으로 채워져 시트와 CSV 로 나갑니다.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

VAT_RATE = Decimal("0.1")
CREDITS_PER_MINUTE = 60
_MARK = "실제 분당단가"

# 「부가세가 붙는 계약인가」 — `won.vat_applicable` 과 **같은 규칙**입니다. NULL 은 「아직 안
# 고름」이라 통화로 추정합니다(그 칸이 생기기 전의 행 수백 개가 그렇습니다). 이 조건을
# `vat_applicable = 1` 로만 쓰면 옛 원화 계약이 전부 빠져나가 금액이 안 옮겨집니다.
_APPLICABLE = "(vat_applicable = 1 OR (vat_applicable IS NULL AND currency = 'KRW'))"


def _num(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _rate(text_value: Decimal, credits: int) -> str:
    """분당 단가를 사람이 읽는 글자로. 딱 떨어지면 소수점을 안 적습니다."""
    rate = (text_value / (Decimal(credits) / CREDITS_PER_MINUTE)).quantize(Decimal("0.01"))
    whole = rate.to_integral_value()
    if rate == whole:
        return f"{int(whole):,}"
    return f"{rate:,}"


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "client_contracts" not in set(inspector.get_table_names()):
        logger.info("0123: client_contracts 없음, 건너뜁니다.")
        return
    columns = {c["name"] for c in inspector.get_columns("client_contracts")}
    if "amount_excl_vat" not in columns or "vat_included" not in columns:
        logger.info("0123: 이미 합쳐져 있습니다, 건너뜁니다.")
        return

    with engine.begin() as conn:
        # ① VAT 미포함 기준이던 계약 — 그 행들에만 손댑니다.
        rows = conn.execute(text(
            "SELECT client_id, seq, currency, credits, amount_incl_vat, amount_excl_vat, note "
            "FROM client_contracts "
            f"WHERE vat_included = 0 AND {_APPLICABLE}"
        )).mappings().all()

        moved = noted = 0
        for row in rows:
            incl, excl = _num(row["amount_incl_vat"]), _num(row["amount_excl_vat"])
            # 그 계약의 분당 단가가 지금까지 어느 금액에서 나왔는가 — 옛 `won.billing_amount`.
            basis = excl if excl is not None else (incl / (1 + VAT_RATE) if incl else None)

            # 계약비고를 **먼저** 적습니다. 열을 지운 뒤에는 이 숫자를 되살릴 수 없습니다.
            credits = row["credits"] or 0
            note = row["note"] or ""
            if basis and credits > 0 and _MARK not in note:
                line = (f"{_MARK} {_rate(basis, credits)} "
                        f"{row['currency'] or ''}/분 (VAT 미포함 기준)").replace("  ", " ")
                conn.execute(
                    text("UPDATE client_contracts SET note = :note "
                         "WHERE client_id = :cid AND seq = :seq"),
                    {"note": f"{note}\n{line}" if note.strip() else line,
                     "cid": row["client_id"], "seq": row["seq"]},
                )
                noted += 1

            # ② 미포함 칸에 적었던 숫자를 계약금액으로.
            if excl is None or excl == incl:
                continue
            conn.execute(
                # **글자로 묶습니다.** sqlite3 는 `Decimal` 을 바인딩하지 못합니다
                # (「type 'decimal.Decimal' is not supported」) — 운영은 Postgres 라 안
                # 걸리지만 개발자 DB 와 테스트가 그 자리에서 터집니다. 두 DB 모두 NUMERIC
                # 칸에 들어오는 숫자 글자를 그대로 받습니다.
                text("UPDATE client_contracts SET amount_incl_vat = :amount "
                     "WHERE client_id = :cid AND seq = :seq"),
                {"amount": str(excl), "cid": row["client_id"], "seq": row["seq"]},
            )
            moved += 1
            # 되돌릴 자료는 이 줄뿐입니다 — 운영 DB 는 개발망에서 조회할 수 없습니다.
            logger.info("0123: %s-%s 계약금액 %s → %s (옛 공급가).",
                        row["client_id"], row["seq"], incl, excl)

        for column in ("vat_included", "amount_excl_vat"):
            conn.execute(text(f"ALTER TABLE client_contracts DROP COLUMN {column}"))

    logger.info("0123: 계약금액을 VAT 포함 한 칸으로 합쳤습니다 — 금액 %s행 이동, "
                "계약비고 %s행에 분당단가 기재, vat_included·amount_excl_vat 삭제.",
                moved, noted)
