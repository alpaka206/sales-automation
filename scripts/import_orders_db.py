r"""수주 DB 33행을 새 탭 구조로 나눠 넣는다. 수주 DB 는 읽기만 한다.

한 행이 한 계약이라, 나누면 이렇게 흩어진다:

    수주 DB 1행  →  고객 기본 정보 (회사, 이미 있으면 빈칸만 채움)
                 +  계약 및 결제 정보 1행  (Perso 계정·플랜 포함)
                 +  크레딧 지급 현황 1행
                 +  결제 현황 1행 (일시불만)

**모르는 것은 지어내지 않는다.** 수주 DB 에 없는 값(공급가, 플랜 티어, 갱신 계획)은 빈칸으로
두고, 분납 내역을 모르는 할부 7건은 결제 행을 만들지 않는다 — 검증 탭이 "금액 불일치" 로
잡아 주는 편이, 그럴듯한 숫자를 넣어 맞는 것처럼 보이게 하는 것보다 낫다.

크레딧도 마찬가지다. 계약 크레딧 열은 행마다 뜻이 다르다(어떤 행은 분, 어떤 행은 크레딧,
최근 4건은 1분=60크레딧). 그래서 **적힌 값을 그대로** 넣고, 시트의 「산정 크레딧」이
공급가 ÷ 단가 × 60 으로 다시 계산해 어긋나는 행을 짚게 한다. 공급가가 비어 있으면 산정도
비므로 지금은 조용하지만, 공급가를 채우는 순간 틀린 행이 드러난다.

    .\.venv\Scripts\python.exe -m scripts.import_orders_db          # 무엇이 들어갈지만 보여준다
    .\.venv\Scripts\python.exe -m scripts.import_orders_db --write  # 실제로 넣는다
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

from scripts.build_won_sheets import (  # noqa: E402
    CLIENTS_TAB,
    CONTRACTS_TAB,
    CREDITS_TAB,
    PAYMENTS_TAB,
    SPREADSHEET_ID,
    TABS,
    idx,
)

# 수식(ARRAYFORMULA)이 든 열에는 절대 쓰지 않는다. 한 칸만 값으로 덮어도 그 열 전체가
# #REF! 로 깨진다 — 실제로 담당부서 열에 값을 넣었다가 한 번 깼다.
from src.integrations import google_sheets as gs  # noqa: E402

# 수식(ARRAYFORMULA)이 든 열에는 절대 쓰지 않는다. 한 칸만 값으로 덮어도 그 열 전체가
# #REF! 로 깨진다 — 실제로 담당부서 열에 값을 넣었다가 한 번 깼다.
DERIVED = {tab["title"]: set(tab.get("array") or {}) for tab in TABS}

ORDERS_TAB = "수주 DB"
CLAIMS_TAB = "클레임 · 히스토리"

# 수주 DB 의 계약서 열 → 선택지 시트의 계약서 유형. PoC·계약 안함은 계약서 종류가 아니라
# 거래 성격이라 "해당 없음" 으로 접는다 (수주 유형 쪽에서 PoC 로 잡힌다).
_DOC_TYPES = {
    "직접 계약 (docusign)": "직접 계약 / DocuSign",
    "금액 지불 시 협의 내용에 합의 및 기존 약관에 동의": "결제 시 약관 및 협의 내용 동의",
    "세금계산서 발행": "세금계산서 발행",
    "poc": "해당 없음",
    "계약 안함": "해당 없음",
}
_PLAN_STATUS = {"사용 중": "사용중", "종료": "사용 중단"}


def money(text: object) -> float | None:
    digits = re.sub(r"[^\d.]", "", str(text))
    return float(digits) if digits else None


def add_months(iso: str, months: object) -> str:
    try:
        start = date.fromisoformat(str(iso)[:10])
        count = int(float(str(months)))
    except (ValueError, TypeError):
        return ""
    index = start.month - 1 + count
    year, month = start.year + index // 12, index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1])).isoformat()


def unit_price(note: str) -> tuple[str, float] | None:
    """계약 비고에서 분당 단가를 뽑는다. ``/Credit`` 은 **분당이 아니므로** 안 가져온다.

    ``$ 1.75/min`` 은 분당 단가지만 ``₩3,000/Credit`` 은 크레딧당 가격이다. 둘을 같은 칸에
    넣으면 크레딧 산정이 60배 틀린다.
    """
    found = re.search(r"([₩$])\s*([\d,.]+)\s*/\s*(min|분)", note, re.I)
    if not found:
        return None
    try:
        return ("KRW" if found.group(1) == "₩" else "USD", float(found.group(2).replace(",", "")))
    except ValueError:
        return None


def doc_types(text: str) -> str:
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    mapped = []
    for part in parts:
        name = _DOC_TYPES.get(part.lower(), part)
        if name not in mapped:
            mapped.append(name)
    return " + ".join(mapped)


def main() -> None:
    write = "--write" in sys.argv
    svc = gs._build_service()
    sheets = svc.spreadsheets()

    def read(rng: str) -> list[list[str]]:
        return (
            sheets.values().get(spreadsheetId=SPREADSHEET_ID, range=rng).execute().get("values")
            or []
        )

    rows = [
        line + [""] * (33 - len(line))
        for line in read(f"'{ORDERS_TAB}'!A3:AG400")
        if line and str(line[0]).replace(",", "").strip().isdigit()
    ]
    print(f"수주 DB {len(rows)}행을 읽었습니다.\n")

    # 같은 Client ID 안에서 수주일 순서가 곧 계약 차수다.
    by_client: dict[int, list[list[str]]] = {}
    for line in rows:
        by_client.setdefault(int(str(line[0]).replace(",", "").strip()), []).append(line)
    for lines in by_client.values():
        lines.sort(key=lambda line: str(line[3]))

    clients: dict[int, dict] = {}
    contracts: list[dict] = []
    credits: list[dict] = []
    payments: list[dict] = []
    claims: list[dict] = []

    for client_id, lines in sorted(by_client.items()):
        first = lines[0]
        clients[client_id] = {
            "A": client_id,
            "C": str(first[4]).strip(),
            "I": str(first[3]).strip(),  # 최초 수주일 = 가장 이른 수주일
            "J": _PLAN_STATUS.get(str(lines[-1][8]).strip(), ""),  # 마지막 계약의 상태
        }
        for seq, line in enumerate(lines, start=1):
            note = str(line[14]).strip()
            months = str(line[11]).strip()
            unit = unit_price(note)
            lump = str(line[9]).strip() == "일시불"
            amount = money(line[13])
            contracts.append(
                {
                    "A": client_id, "C": seq,
                    "E": "PoC" if "poc" in (note + str(line[5])).lower() else "MRR",
                    "F": str(line[3]).strip(),
                    "G": add_months(line[3], months),
                    "I": doc_types(line[5]),
                    "J": money(line[23]) or "",
                    "K": str(line[12]).strip(),
                    "L": amount or "",
                    "N": unit[0] if unit else "",
                    "O": unit[1] if unit else "",
                    "Q": str(line[6]).strip(),
                    "R": str(line[9]).strip(),
                    "S": 1 if lump else "",
                    "T": str(line[7]).strip(),
                    "U": str(line[10]).strip(),
                    "V": note,
                    "Y": " / ".join(x for x in (str(line[26]).strip(), str(line[30]).strip()) if x),
                    "AC": str(line[18]).strip(),
                    "AD": str(line[15]).strip(),
                    "AE": str(line[17]).strip(),
                    "AF": add_months(line[17], months),
                    "AH": money(line[19]) or "", "AI": money(line[20]) or "",
                    "AJ": money(line[21]) or "", "AK": money(line[22]) or "",
                    "AL": str(line[16]).strip(),
                }
            )
            history = str(line[24]).strip()
            if money(line[23]):
                credits.append(
                    {
                        "A": client_id, "C": seq, "D": 1,
                        "F": str(line[7]).strip() or str(line[3]).strip(),
                        "G": money(line[23]),
                        "H": str(line[25]).strip() if history else "",
                        # 히스토리가 있으면 실제로 준 것이고, 없으면 확인이 안 된 것이다.
                        "I": "지급 완료" if history else "지급 예정",
                        "J": history,
                    }
                )
            if lump and amount:
                payments.append(
                    {
                        "A": client_id, "C": seq, "D": 1,
                        "F": str(line[7]).strip() or str(line[3]).strip(),
                        "G": amount, "H": "입금 완료",
                    }
                )
            if str(line[27]).strip():
                claims.append(
                    {
                        "A": client_id, "C": seq,
                        "D": str(line[27]).strip(),
                        "E": str(line[29]).strip() or str(line[3]).strip(),
                        "F": str(line[28]).strip(),
                        "G": "조치 완료" if str(line[29]).strip() else "접수",
                        "H": str(line[29]).strip(),
                    }
                )

    installments = sum(1 for line in rows if str(line[9]).strip() != "일시불")
    print(f"  고객 기본 정보 : {len(clients)}곳 (이미 있으면 빈칸만 채웁니다)")
    print(f"  계약 및 결제 정보: {len(contracts)}건 · 재계약 "
          f"{sum(1 for v in by_client.values() if len(v) > 1)}곳")
    print(f"  크레딧 지급 현황 : {len(credits)}행")
    print(f"  결제 현황       : {len(payments)}행 (할부 {installments}건은 분납 내역을 몰라 "
          f"만들지 않습니다 — 검증이 '금액 불일치' 로 잡습니다)")
    print(f"  클레임          : {len(claims)}건")
    print(f"  분당 단가를 뽑은 계약: {sum(1 for c in contracts if c['P'])}건 "
          f"(/Credit 표기는 분당이 아니라 건너뜁니다)")

    if not write:
        print("\n--- 계약 미리보기 ---")
        for c in contracts[:6]:
            print(f"  {c['A']} {c['C']}차 {c['G']}~{c['H']} {c['L']} {c['M']:,.0f} "
                  f"크레딧 {c['K'] or '-'} 단가 {c['P'] or '-'}")
        print("\n넣으려면 --write 를 붙이세요.")
        return

    # ---- 자연키로 채워 넣는다. 이미 있는 행은 빈칸만 채운다 -------------------
    plans = [
        (CLIENTS_TAB, ("A",), list(clients.values())),
        (CONTRACTS_TAB, ("A", "C"), contracts),
        (CREDITS_TAB, ("A", "C", "D"), credits),
        (PAYMENTS_TAB, ("A", "C", "D"), payments),
        (CLAIMS_TAB, ("A", "C", "D", "E"), claims),
    ]
    data = []
    for tab, keys, wanted in plans:
        existing = read(f"'{tab}'!A2:AM1000")
        width = max((len(line) for line in existing), default=0)
        seen: dict[tuple, int] = {}
        free = []
        for offset, line in enumerate(existing):
            line = list(line) + [""] * (width - len(line))
            key = tuple(str(line[idx(k)]).strip() if idx(k) < width else "" for k in keys)
            if any(key):
                seen.setdefault(key, offset + 2)
            else:
                free.append(offset + 2)
        free += list(range(len(existing) + 2, 1001))
        for row in wanted:
            key = tuple(str(row.get(k, "")).strip() for k in keys)
            number = seen.get(key)
            if number is None:
                number = free.pop(0)
                cells = row
            else:  # 이미 있는 행: 비어 있는 칸만 채운다
                line = list(existing[number - 2]) + [""] * width
                cells = {
                    letter: value
                    for letter, value in row.items()
                    if str(value).strip()
                    and not str(line[idx(letter)] if idx(letter) < len(line) else "").strip()
                }
            for letter, value in cells.items():
                if letter in DERIVED.get(tab, ()):
                    continue
                if str(value).strip() != "":
                    data.append({"range": f"'{tab}'!{letter}{number}", "values": [[value]]})
        print(f"  '{tab}' 준비 완료")

    for chunk in range(0, len(data), 500):
        sheets.values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": data[chunk:chunk + 500]},
        ).execute()
    print(f"\n{len(data)}칸을 넣었습니다. 수주 DB 는 읽기만 했습니다.")


if __name__ == "__main__":
    main()
