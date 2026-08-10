r"""Payments 탭의 분납 스케줄을 결제 현황으로 옮긴다. 할부 계약의 마지막 빈칸이다.

수주 DB 에는 "할부" 라고만 적혀 있고 몇 회에 얼마씩인지가 없었다. 그래서 처음 옮길 때
할부 7건은 결제 행을 못 만들었고, 검증이 "금액 불일치" 로 잡고 있었다. 그 내역이
Payments 탭에 있다 — 총 분납 횟수 · 회차별 납부 금액 · 결제 주기(개월) · 현재 분납 차수.

현재 분납 차수까지는 입금 완료, 그 뒤는 입금 전으로 놓는다. 날짜는 최초 결제일부터
결제 주기 간격이다.

    .\.venv\Scripts\python.exe -m scripts.import_payment_schedule          # 미리보기
    .\.venv\Scripts\python.exe -m scripts.import_payment_schedule --write
"""

from __future__ import annotations

import calendar
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.tls import use_os_trust_store  # noqa: E402

use_os_trust_store()

from scripts.build_won_sheets import PAYMENTS_TAB, SPREADSHEET_ID, TABS  # noqa: E402
from src.integrations import google_sheets as gs  # noqa: E402

DERIVED = {tab["title"]: set(tab.get("array") or {}) for tab in TABS}


def number(text: object) -> float | None:
    digits = re.sub(r"[^\d.]", "", str(text))
    return float(digits) if digits else None


def add_months(iso: str, months: int) -> str:
    try:
        start = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return ""
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1])).isoformat()


def main() -> None:
    write = "--write" in sys.argv
    sheets = gs._build_service().spreadsheets()

    def read(rng: str) -> list[list[str]]:
        return sheets.values().get(spreadsheetId=SPREADSHEET_ID, range=rng).execute().get(
            "values"
        ) or []

    rows = read("'Payments'!A1:Y60")
    head = {name: i for i, name in enumerate(rows[0])}
    schedule: dict[str, dict] = {}
    for line in rows[1:]:
        if not (line and str(line[0]).strip().isdigit()):
            continue

        def cell(name: str, line=line) -> str:
            index = head.get(name, 999)
            return str(line[index]).strip() if index < len(line) else ""

        total = number(cell("총 분납 횟수"))
        if not total or total <= 1:
            continue  # 일시불은 이미 들어가 있다
        schedule[str(line[0]).strip()] = {
            "total": int(total),
            "each": number(cell("회차별 납부 금액")),
            "every": int(number(cell("결제 주기(개월)")) or 1),
            "done": int(number(cell("현재 분납 차수")) or 0),
            "first": cell("최초 결제일"),
        }

    # 그 Client ID 의 어느 계약이 할부인지는 계약 탭에서 찾는다 (결제 방식 = 할부).
    contracts = [
        line
        for line in read("'계약 및 결제 정보'!A2:T60")
        if line and str(line[0]).strip().isdigit()
    ]
    existing = {
        (str(r[0]).strip(), str(r[2]).strip())
        for r in read(f"'{PAYMENTS_TAB}'!A2:D200")
        if r and str(r[0]).strip().isdigit()
    }

    planned: list[dict] = []
    for line in contracts:
        line = list(line) + [""] * 20
        client_id, seq, method = str(line[0]).strip(), str(line[2]).strip(), str(line[17]).strip()
        if method != "할부" or (client_id, seq) in existing:
            continue
        plan = schedule.get(client_id)
        if not plan or not plan["each"]:
            print(f"  {client_id} {seq}차: Payments 에 분납 내역이 없습니다 — 건너뜁니다.")
            continue
        base = plan["first"] or str(line[19]).strip()
        for no in range(1, plan["total"] + 1):
            planned.append(
                {
                    "A": int(client_id), "C": int(seq), "D": no,
                    "F": add_months(base, plan["every"] * (no - 1)),
                    "G": plan["each"],
                    "H": "입금 완료" if no <= plan["done"] else "입금 전",
                }
            )
        print(f"  {client_id} {seq}차: {plan['total']}회 × {plan['each']:,.0f} "
              f"({plan['every']}개월 간격, {plan['done']}회 입금)")

    print(f"\n결제 현황에 {len(planned)}행을 넣습니다.")
    if not write:
        print("넣으려면 --write 를 붙이세요.")
        return

    used = len([r for r in read(f"'{PAYMENTS_TAB}'!A2:A200") if r and str(r[0]).strip()])
    data = []
    for offset, row in enumerate(planned):
        number_row = used + 2 + offset
        for letter, value in row.items():
            if letter in DERIVED.get(PAYMENTS_TAB, ()):
                continue
            data.append({"range": f"'{PAYMENTS_TAB}'!{letter}{number_row}", "values": [[value]]})
    for chunk in range(0, len(data), 500):
        sheets.values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": data[chunk:chunk + 500]},
        ).execute()
    print(f"{len(data)}칸을 넣었습니다.")


if __name__ == "__main__":
    main()
