"""계약 금액을 **VAT 포함 한 칸**으로 합칩니다 (2026-09-21 운영자 지시).

운영자 문장 그대로:

    계약금액은 모두 VAT 포함만 으로 바꿀거야
    기존에 vat 포함이였던 것들은 그대로 쓰면 되고
    포함, 미포함 둘다 써있던건 미포함만 납겨두면 되고
    미포함만 써있던건 그걸 vat 포함에 작성해두면 돼
    분당단가가 VAT미포함인 건들은 "계약비고"에 실제 분당단가 추가로 기재하도록 해줘

**세 줄 중 둘은 값 규칙이고 하나는 칸 규칙입니다.** 이것을 한 번 잘못 읽어 미포함 숫자를
계약금액으로 옮겼다가 운영자가 바로 잡아 줬습니다(「그러면 vat 포함으로 하지않았어 우리?」).
DB 를 보면 왜 그렇게 읽어야 하는지가 분명합니다 — 옛 저장 경로(`_settle_amounts`)가 국내
계약이면 **언제나 두 칸을 다 채웠고** `vat_included` 는 어느 쪽이 계약서에 적힌 값인지를
표시만 했습니다:

===========================  =============================================
운영자 문장                  실제 DB
===========================  =============================================
포함만 써있던 건             임포트 행(`import_orders_db` 는 L 만 씁니다) + 해외 계약
포함·미포함 둘 다            콘솔로 저장한 국내 계약 **전부**
미포함만 써있던 건           그렇게 쓰는 경로가 없어 **아마 0건**
===========================  =============================================

즉 ①·③ 이 값 규칙(**포함을 쓰고**, 포함이 없으면 미포함을 올려 쓴다)이고 ② 는 칸 규칙
(칸을 하나만 남겨라)입니다. 그래서 이 이관은 **금액을 거의 안 움직입니다** — `amount_incl_vat`
가 비어 있고 `amount_excl_vat` 만 있는 행만 올려 씁니다(③ 의 안전망, 운영에는 없을 것입니다).

`amount_excl_vat` 와 `vat_included` 를 지우고 `amount_incl_vat` 하나를 남깁니다. 공급가는
저장하지 않고 총액 ÷ 1.1 로 되짚습니다(`won.supply_amount`).

**총액·월간 MRR·예상 MRR·월별 시리즈는 한 숫자도 안 움직입니다.** 마감한 달의 값도 그대로이고,
분납 회차도 옛 총액 기준 그대로라 어긋나지 않습니다. 반대로 미포함을 옮겼다면 그 건들의
총액이 10% 내려앉고 회차 합계만 남아 수금율이 100% 를 넘었을 것입니다.

**움직이는 것은 분당 단가 하나입니다.** 기준이 「계약서에 적힌 금액」에서 「계약금액(=총액)」으로
바뀌므로, VAT 미포함 기준이던 계약의 화면 단가가 10% 올라갑니다. 그 사실을 아는 칸이
`vat_included` 뿐이었고 그것이 사라지므로, **열을 지우기 전에** 계약비고에 한 줄로 남깁니다:
「실제 분당단가 … (VAT 미포함 기준)」. 운영자의 마지막 줄이 요구한 것이 바로 이것입니다.
덧붙이기만 하고(운영자 자유 텍스트입니다) 같은 줄이 이미 있으면 건너뜁니다.

같이 고쳐야 했던 것 하나: **워크북 「검증」 탭의 산정 크레딧 수식**(`scripts/build_won_sheets.py`,
M ÷ O → L ÷ O). 공급가가 언제나 총액 ÷ 1.1 이 되면서 그 열이 계약 크레딧 ÷ 1.1 로 나와
**모든 행**이 「크레딧 산정 불일치」로 찍히기 때문입니다 — 전부 빨개지면 그 열이 하던 일(진짜
어긋난 행을 눈에 걸리게 하는 것)이 죽습니다. 살아 있는 워크북에는 배포·동기화 **뒤**
`python -m scripts.build_won_sheets --refresh-derived` 를 돌려야 반영됩니다(먼저 돌리면 L·M·O
가 아직 옛 값이라 반대 방향으로 깨집니다).

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
# 고름」이라 통화로 추정합니다(그 칸이 생기기 전의 행 수백 개가 그렇습니다). NULL 가지를
# 빼면 옛 원화 계약이 전부 빠져나가 계약비고를 못 받습니다.
#
# **`= 1` / `= 0` 으로 쓰면 Postgres 가 거절합니다** — `operator does not exist: boolean =
# integer`. SQLite 는 boolean 을 정수로 들고 있어 통과하므로 **로컬 테스트로는 절대 안
# 잡힙니다**: 2026-09-21 에 이 이관이 그렇게 배포에서 죽었습니다(빌드 실패, 앞선 0122 만
# 적용된 채로 남아 그 11행이 옛 드롭다운에 없는 값을 든 상태가 됐습니다). `IS TRUE` ·
# `IS FALSE` 는 양쪽 다 되고 뜻이 하나입니다.
_APPLICABLE = "(vat_applicable IS TRUE OR (vat_applicable IS NULL AND currency = 'KRW'))"


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
            f"WHERE vat_included IS FALSE AND {_APPLICABLE}"
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

            # ② **포함 칸이 비어 있을 때만** 미포함을 올려 씁니다 — 운영자 문장 ③ 의
            # 안전망입니다. 채워져 있으면 그 값이 계약금액이고, 그것이 「모두 VAT 포함」의
            # 뜻입니다. 여기서 미포함을 덮어쓰면 총액이 10% 내려앉고 분납 회차만 옛 총액으로
            # 남아 수금율이 100% 를 넘습니다.
            if incl is not None or excl is None:
                continue
            conn.execute(
                # **글자로 묶고 CAST 로 못박습니다.** sqlite3 는 `Decimal` 을 바인딩하지
                # 못하고(「type 'decimal.Decimal' is not supported」) 운영은 Postgres 라
                # 안 걸립니다 — 즉 어느 한쪽에서만 터지는 종류입니다. 글자를 NUMERIC 칸에
                # 넣는 암묵적 캐스트도 두 DB 가 같다고 믿을 자리가 아니라 직접 적습니다.
                text("UPDATE client_contracts SET amount_incl_vat = CAST(:amount AS NUMERIC) "
                     "WHERE client_id = :cid AND seq = :seq"),
                {"amount": str(excl), "cid": row["client_id"], "seq": row["seq"]},
            )
            moved += 1
            # 운영 DB 는 개발망에서 조회할 수 없으므로, 몇 행이 그랬는지를 아는 길은 이 줄뿐입니다.
            logger.info("0123: %s-%s 계약금액이 비어 있어 공급가 %s 를 올려 썼습니다.",
                        row["client_id"], row["seq"], excl)

        for column in ("vat_included", "amount_excl_vat"):
            conn.execute(text(f"ALTER TABLE client_contracts DROP COLUMN {column}"))

    logger.info("0123: 계약금액을 VAT 포함 한 칸으로 합쳤습니다 — 빈 금액 %s행을 공급가로 "
                "채우고, 계약비고 %s행에 VAT 미포함 기준 분당단가를 적었습니다. "
                "vat_included·amount_excl_vat 삭제.", moved, noted)
