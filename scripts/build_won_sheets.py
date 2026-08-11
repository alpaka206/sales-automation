r"""수주 고객 시트를 영업 워크북에 짓는다.

설계는 세 줄로 요약된다.

1. **고객사 이름은 「고객 기본 정보」 한 곳에만 있다.** 계약·회차·클레임 네 탭과
   Inbound DB 가 Client ID 로 거기를 조회한다. 그래서 이 탭은 **아무 데도 조회하지 않는다** —
   조회하는 순간 반대 방향이 순환 참조가 되어 양쪽 다 #REF! 가 된다. 화살표는 한 방향뿐이다.

2. **파생 열은 열당 수식 한 칸이다**(ARRAYFORMULA, 2행). 예전에는 같은 수식을 400줄 복사해
   깔아 뒀는데, 그러면 (a) 그 줄 수가 곧 입력 한계가 되고, (b) 누가 한 줄만 지우면 그 행만
   조용히 계산을 멈추며, (c) 셀이 수만 개 늘어난다. 한 칸짜리는 아래로 저절로 자란다.
   대신 그 열에 값을 쓰면 배열 전체가 깨지므로, 콘솔이 쓰는 열과는 절대 겹치지 않게 한다
   (src/agents/won_sheets.py 의 ``owned``). 사람이 실수로 지우지 않도록 경고용 보호도 건다.

3. **탭은 표 하나다.** 표 아래에 작성 메모를 달지 않는다 — 표가 자라면 그 자리에 데이터가
   와야 하고, 메모는 열 제목의 셀 메모로 옮기면 필요한 순간에 바로 보인다.

    .\.venv\Scripts\python.exe -m scripts.build_won_sheets            # 만들기
    .\.venv\Scripts\python.exe -m scripts.build_won_sheets --replace  # 지우고 다시
    ... --spreadsheet <워크북 ID>        # 다른 워크북에
    ... --import-inbound                # 고객 기본 정보를 Inbound DB 에서 새로 만든다
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.tls import use_os_trust_store  # noqa: E402

use_os_trust_store()

from src.common.config import settings  # noqa: E402
from src.integrations import google_sheets as gs  # noqa: E402


def _target() -> str:
    """``--spreadsheet <id>`` 로 다른 워크북에도 짓는다. 없으면 설정된 워크북."""
    if "--spreadsheet" in sys.argv:
        return sys.argv[sys.argv.index("--spreadsheet") + 1].strip()
    return settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip()


SPREADSHEET_ID = _target()
INBOUND = settings.GOOGLE_SHEETS_INBOUND_TAB.strip() or "Inbound DB"
INBOUND_FIRST_ROW = 3  # 1행이 묶음 라벨, 2행이 헤더다.
GRID_ROWS = 1000  # 표의 물리적 크기. 수식이 여기까지 자란다.

CLIENTS_TAB = "고객 기본 정보"
CONTRACTS_TAB = "계약 및 결제 정보"
CREDITS_TAB = "크레딧 지급 현황"
PAYMENTS_TAB = "결제 현황"

# 번호대 → 담당부서. 3000 Interactive / 4000 AX 만 다르고 나머지는 GTM 이다.
_DEPARTMENT = ((9000, "GTM"), (4000, "AX"), (3000, "Interactive"), (2000, "GTM"), (1000, "GTM"))


def d(serial: int) -> str:
    """엑셀 날짜 일련번호 → YYYY-MM-DD. (엑셀·시트 모두 1899-12-30 이 0일)"""
    return (date(1899, 12, 30) + timedelta(days=serial)).isoformat()


def col(index: int) -> str:
    letter = ""
    while index >= 0:
        index, rem = divmod(index, 26)
        letter = chr(65 + rem) + letter
        index -= 1
    return letter


def idx(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def department_of(client_id: int) -> str:
    return next((name for floor, name in _DEPARTMENT if client_id >= floor), "")


# --------------------------------------------------------------------------- #
# 수식 — 전부 ARRAYFORMULA 다. 2행 한 칸이 열 전체를 맡는다.
# --------------------------------------------------------------------------- #
def _array(body: str) -> str:
    return f"=ARRAYFORMULA({body})"


def lookup_company(key: str = "$A$2:$A") -> str:
    """Client ID → 고객 기본 정보의 고객사. 이 워크북에서 가장 많이 쓰는 조회다."""
    return _array(
        f"IF(ISNUMBER({key}),IFERROR(VLOOKUP({key},'{CLIENTS_TAB}'!$A:$C,3,0),\"\"),\"\")"
    )


def _pair(tab: str, left: str, right: str) -> str:
    """(Client ID, 계약 차수) 복합키를 문자열 하나로. VLOOKUP·SUMIF 가 배열로 받는다."""
    return f"'{tab}'!${left}$2:${left}&\"|\"&'{tab}'!${right}$2:${right}"


def count_by_contract() -> str:
    """전체 회차 = 그 계약의 행 수. 매 행에 손으로 적으면 회차를 하나 더할 때마다 앞선
    행들이 전부 낡는다 — 실제로 2102 가 '2회 지급인데 전체 1회' 로 어긋나 있었다."""
    return _array(
        'IF(ISNUMBER($A$2:$A),COUNTIF($A$2:$A&"|"&$C$2:$C,$A$2:$A&"|"&$C$2:$C),"")'
    )


def sum_by_contract(tab: str, total: str, extra: str = "") -> str:
    """검증 탭의 합계. SUMIF 는 조건이 배열이면 결과도 배열이라 한 칸으로 끝난다."""
    keys = _pair(tab, "A", "C")
    mine = "$A$2:$A&\"|\"&$B$2:$B"
    if extra:
        keys += f'&"|"&\'{tab}\'!${extra}$2:${extra}'
    return _array(
        f'IF(ISNUMBER($A$2:$A),SUMIF({keys},{mine},\'{tab}\'!${total}$2:${total}),"")'
    )


CHOICES = [
    ["고객 종류", "산업 분야", "담당부서", "플랜 상태", "수주 유형", "계약서 유형", "통화", "결제 수단", "결제 방식", "플랜", "갱신 계획", "크레딧 상태", "결제 상태", "클레임 진행상황"],
    ["GTM Inbound", "크리에이터(개인)", "GTM", "사용중", "MRR", "해당 없음", "KRW", "Stripe", "일시불", "Business Tier 1", "갱신 예정", "지급 완료", "입금 완료", "접수"],
    ["GTM Outbound", "교육", "Interactive", "세팅중", "PoC", "직접 계약 / DocuSign", "USD", "포트원", "할부", "Business Tier 2", "협의 중", "지급 예정", "입금 전", "조치 진행 중"],
    ["Interactive", "MCN", "AX", "사용 중단", "", "결제 시 약관 및 협의 내용 동의", "", "계좌이체", "", "Business Tier 3", "미정", "", "", "조치 완료"],
    ["AX", "의료", "", "", "", "세금계산서 발행", "", "", "", "Enterprise", "본계약 검토 중", "", "", ""],
    ["2025 Inbound", "종교", "", "", "", "", "", "", "", "", "갱신 안함", "", "", ""],
    ["", "기업", "", "", "", "", "", "", "", "", "갱신 완료", "", "", ""],
    ["", "대행사"], ["", "확인 안 됨"], ["", "제작사/엔터사"], ["", "스포츠"],
    ["", "뷰티"], ["", "공공기관"], ["", "출판"], ["", "제조"], ["", "보안"],
]


def choices(name: str) -> list[str]:
    """선택지 목록. 시트가 아니라 여기가 원본이다 — 드롭다운 규칙에 값이 직접 들어간다."""
    column = CHOICES[0].index(name)
    return [
        row[column]
        for row in CHOICES[1:]
        if column < len(row) and str(row[column]).strip()
    ]


_CLIENT_ID_NOTE = (
    "이 행이 콘솔(수주 고객 화면)의 것인지는 Client ID 로 정해집니다.\n\n"
    "· 콘솔에 있는 고객의 행은 콘솔이 관리합니다. 시트에서 고쳐도 다음 저장 때 "
    "콘솔 값으로 돌아갑니다 — 고칠 것은 콘솔에서 고쳐 주세요.\n"
    "· 콘솔에 없는 Client ID 의 행은 손으로 쓴 것이라 건드리지 않습니다. 나중에 그 고객이 "
    "콘솔에 생기면 콘솔이 그 행을 이어받습니다.\n"
    "· 콘솔에서 지운 항목(클레임 등)은 이 시트에서도 그 행이 비워집니다."
)
_FORMULA_NOTE = (
    "수식 칸입니다. 2행 한 칸이 이 열 전체를 계산하므로 값을 직접 넣으면 열 전체가 "
    "#REF! 로 깨집니다. 고칠 것이 있으면 계산의 재료가 되는 칸을 고쳐 주세요."
)

TABS: list[dict] = [
    {
        "title": CLIENTS_TAB,
        # 다시 지을 때 여기 있던 내용을 그대로 되돌려 놓는다 (모든 데이터 탭 공통).
        "keep": True,
        "freeze_cols": 3,
        # 이 워크북의 원본. 사람 이름과 연락처는 여기 없다 — 담당자는 바뀌고 회사는 안
        # 바뀌는데 한 행에 같이 두면 어느 칸이 바뀔 수 있는 칸인지 아무도 모른다.
        "headers": [
            "Client ID *", "고객 종류", "고객사 *", "Website URL", "산업 분야", "국가", "담당부서",
            "최초 연락일", "최초 수주일", "플랜 상태",
        ],
        "array": {
            # 고객 종류도 담당부서도 번호대에서 나온다. 저장하면 둘이 어긋난다 —
            # 실제 163곳 전부 번호대와 일치했으니 적어 둘 이유가 없다.
            "B": _array(
                'IF(ISNUMBER($A$2:$A),IFERROR(IFS($A$2:$A>=9000,"2025 Inbound",'
                '$A$2:$A>=4000,"AX",$A$2:$A>=3000,"Interactive",'
                '$A$2:$A>=2000,"GTM Outbound",$A$2:$A>=1000,"GTM Inbound"),""),"")'
            ),
            "G": _array(
                'IF($B$2:$B="","",IF(($B$2:$B="Interactive")+($B$2:$B="AX"),$B$2:$B,"GTM"))'
            ),
        },
        "seed": [
            {"A": 1108, "C": "서울대학교", "E": "교육", "F": "한국", "G": "GTM",
             "I": "2025-12-31", "J": "사용중"},  # 최초 수주일 = 1차 계약 날짜
            {"A": 2102, "C": "집나간 햄지", "E": "크리에이터(개인)", "G": "GTM",
             "I": d(46234), "J": "사용중"},
        ],
        "auto": "BG",
        "dates": "HI",
        "dv": {"E": "산업 분야", "J": "플랜 상태"},
        "widths": {"A": 12, "B": 16, "C": 24, "D": 26, "E": 16, "F": 12, "G": 12, "H": 14,
                   "I": 14, "J": 12},
        "note": {
            "A": _CLIENT_ID_NOTE,
            "C": "이 워크북에서 고객사 이름이 적히는 유일한 곳입니다. 계약·회차·클레임 탭과 "
                 "Inbound DB 가 Client ID 로 여기를 조회합니다 — 여기서 고치면 "
                 "전부 따라 바뀝니다.",
            "D": "콘솔에 없는 칸이라 시트에서만 관리합니다. 콘솔이 덮어쓰지 않습니다.",
            "H": "Inbound DB 의 가장 이른 문의 날짜입니다. 콘솔이 덮어쓰지 않습니다.",
            "I": "비어 있으면 아직 문의만 하고 수주 전인 회사입니다.",
        },
    },
    {
        "title": CONTRACTS_TAB,
        "keep": True,
        "freeze_cols": 2,
        "headers": [
            "Client ID *", "고객사", "계약 차수 *", "Ticket ID",
            "수주 유형 *", "계약 시작일 *", "계약 종료일 *", "계약 개월수", "계약서 유형",
            "계약 크레딧 *", "통화 *", "총 계약금액 (VAT 포함) *", "공급가 (VAT 제외) *",
            "분당 단가 통화", "분당 단가", "적용 환율", "결제 수단", "결제 방식", "총 분납 횟수",
            "최초 결제일", "Billing Email", "계약 비고", "갱신 계획", "사용 중단 이유", "비고 메모",
            "매출 인식 시작 월 (YYYY-MM)", "월간 매출 (VAT 포함)",
            # 아래는 Perso 계정·플랜. 계약과 1:1 이라 탭을 따로 두면 Client ID·고객사·차수
            # 세 열을 다시 적을 뿐이다 (db/models.py 가 같은 말을 한다).
            "플랜", "플랜명", "Perso Email", "플랜 시작일", "플랜 만료일", "잔여일수",
            "Account Invitation Limit", "Queue limit", "Concurrent Jobs", "Space 개수", "space_seq",
            "담당",
        ],
        "array": {
            "B": lookup_company(),
            "H": _array(
                "IF(ISNUMBER($F$2:$F)*ISNUMBER($G$2:$G),"
                'ROUND(($G$2:$G-$F$2:$F)/30.4375,0),"")'
            ),
            "AA": _array(
                'IF(($E$2:$E="MRR")*ISNUMBER($H$2:$H)*($H$2:$H>0),'
                'ROUND($L$2:$L/$H$2:$H,0),"")'
            ),
            "AG": _array('IF(ISNUMBER($AF$2:$AF),$AF$2:$AF-TODAY(),"")'),
        },
        "seed": [
            # 수주 DB 기준 2차 계약이다 (1차는 2025-12-31, --import-orders 가 넣는다).
            {"A": 1108, "C": 2, "E": "MRR", "F": d(46198), "G": d(46563),
             "I": "직접 계약 / DocuSign + 세금계산서 발행", "J": 456120, "K": "KRW",
             "L": 22000000, "M": 20000000, "N": "USD", "O": 1.75, "P": 1503.3637,
             "Q": "계좌이체", "R": "일시불", "S": 1, "T": d(46202),
             "U": "bigdata.coss@gmail.com",
             "V": "계약 비고 원문: $ 1.75/min", "W": "갱신 예정",
             "AB": "Enterprise", "AC": "SNU Biz", "AD": "bigdata.coss@gmail.com",
             "AE": d(46198), "AF": d(46563), "AH": 5, "AI": 10, "AJ": 5, "AK": 3,
             "Y": "크레딧 제공 히스토리 원문: 납부:260624 | 기간:12M | 결제:2천만원(일시불) | 단가:$1.75 | 배분:1S* 456,120CD"},
            {"A": 2102, "C": 1, "E": "MRR", "F": d(46233), "G": d(46417),
             "I": "결제 시 약관 및 협의 내용 동의", "J": 64800, "K": "KRW",
             "L": 1722600, "M": 1566000, "N": "KRW", "O": 1450,
             "Q": "계좌이체", "R": "일시불", "S": 1, "T": d(46233),
             "U": "hjh35550@gmail.com",
             "V": "계약 비고 원문: 1,450원/min · 공급가 ₩1,566,000 (VAT 포함 ₩1,722,600)",
             "AC": "햄지 Biz", "AD": "hjh35550@gmail.com",
             "AE": d(46233), "AF": d(46417), "AH": 10, "AI": 6, "AJ": 4, "AK": 1,
             "Y": "크레딧 제공 히스토리 원문: 납부:7월 말~8월 초 입금 예정 | 기간:6M | 결제: 1,566,000원(일시불) | 단가:1,450원 | 배분:1S*64,800CD / 히스토리 디테일: 30분 분량 테스트용 크레딧 추가 지급"},
        ],
        # USER_ENTERED 로 쓰면 티켓 번호는 지수 표기 숫자가, "2026-08" 은 날짜가 된다.
        "raw": {"D2": "33569285728", "Z3": "2026-08",
                "AL2": "460641, 460640, 455593", "AL3": "584758"},
        "auto": ["B", "H", "AA", "AG"],
        # 두 글자 열(AE·AF)이 있으니 문자열이 아니라 목록으로 준다.
        "dates": ["F", "G", "T", "AE", "AF"],
        "money": ["L", "M", "AA"],
        "ints": ["C", "J", "S", "AG", "AH", "AI", "AJ", "AK"],
        # 분당 단가·적용 환율은 서식을 안 건다. #,##0.## 는 1450 을 "1,450." 으로 그린다.
        "text": ["D", "Z", "AL"],
        "dv": {"E": "수주 유형", "K": "통화", "N": "통화", "Q": "결제 수단", "R": "결제 방식",
               "W": "갱신 계획", "AB": "플랜"},
        "widths": {"A": 12, "B": 18, "C": 11, "D": 15, "E": 11, "F": 13, "G": 13, "H": 12,
                   "I": 26, "J": 13, "K": 9, "L": 19, "M": 17, "N": 13, "O": 11, "P": 12,
                   "Q": 12, "R": 11, "S": 12, "T": 13, "U": 22, "V": 30, "W": 13, "X": 24,
                   "Y": 26, "Z": 16, "AA": 16, "AB": 16, "AC": 18, "AD": 24, "AE": 14,
                   "AF": 14, "AG": 12, "AH": 20, "AI": 12, "AJ": 15, "AK": 11, "AL": 22, "AM": 12},
        "note": {
            "A": _CLIENT_ID_NOTE,
            "C": "재계약은 새 고객이 아니라 같은 Client ID 에 차수를 올린 행입니다.",
            "D": "Inbound DB 에 티켓 번호 열이 없어 자동으로 따라오지 않습니다. "
                 "HubSpot 티켓 번호를 직접 붙여 넣으세요.",
            "J": "1분 = 60크레딧 고정. 공급가(VAT 제외) ÷ 분당 단가 × 60 으로 콘솔이 계산합니다. "
                 "검증 탭이 이 값을 다시 계산해 맞는지 봅니다.",
            "P": "계약 통화와 분당 단가 통화가 다를 때 크레딧 산정에 쓴 환율입니다. "
                 "계약 시점 값이라 오늘 환율로 다시 계산하면 안 됩니다.",
            "Z": "비우면 계약 시작월부터 인식합니다. MRR 에만 적용됩니다.",
            "AM": "이 계약을 맡은 우리 쪽 담당자입니다. 고객이 아니라 계약별로 답니다 — 실제로 1차와 2차의 담당이 다른 고객이 있습니다.",
            "AB": "여기부터는 Perso 계정·플랜입니다. 계약과 1:1 이라 같은 행에 둡니다.",
        },
    },
    {
        "title": CREDITS_TAB,
        "keep": True,
        "freeze_cols": 2,
        "headers": [
            "Client ID *", "고객사", "계약 차수 *", "회차 *", "전체 회차 *", "지급 날짜 *",
            "지급 크레딧 *", "지급자", "상태 *", "메모 (space_seq별 지급량)",
        ],
        "array": {"B": lookup_company(), "E": count_by_contract()},
        "seed": [
            {"A": 1108, "C": 2, "D": 1, "F": d(46197), "G": 456120, "H": "이혜람",
             "I": "지급 완료", "J": "1S* 456,120CD — space 1곳에 전액 배분"},
            {"A": 2102, "C": 1, "D": 1, "F": d(46233), "G": 64800, "H": "이혜람",
             "I": "지급 완료", "J": "1S* 64,800CD — 584758에 전액 배분"},
            {"A": 2102, "C": 1, "D": 2, "F": d(46239), "G": 1800, "H": "이혜람",
             "I": "지급 완료", "J": "계약 외 추가 지급 — 신기능 TEST용 30분 분량 (584758)"},
        ],
        "auto": ["B", "E"],
        "dates": "F",
        "ints": ["C", "D", "E"],
        "money": ["G"],
        "dv": {"I": "크레딧 상태"},
        "widths": {"A": 12, "B": 18, "C": 11, "D": 9, "E": 11, "F": 13, "G": 13, "H": 12,
                   "I": 12, "J": 44},
        "note": {
            "A": _CLIENT_ID_NOTE,
            "F": "예정일이기도 하고 실제 지급일이기도 합니다. 미지급 중 가장 빠른 날이 "
                 "화면의 '다음 지급일' 입니다.",
            "H": "지급 완료 건에만 남습니다. 예정 회차는 비워 두세요.",
        },
    },
    {
        "title": PAYMENTS_TAB,
        "keep": True,
        "freeze_cols": 2,
        "headers": [
            "Client ID *", "고객사", "계약 차수 *", "분납 차수 *", "총 분납 횟수 *",
            "입금 날짜 *", "금액 *", "상태 *",
        ],
        "array": {"B": lookup_company(), "E": count_by_contract()},
        "seed": [
            {"A": 1108, "C": 2, "D": 1, "F": d(46202), "G": 22000000, "H": "입금 완료"},
            {"A": 2102, "C": 1, "D": 1, "F": d(46233), "G": 1722600, "H": "입금 완료"},
        ],
        "auto": ["B", "E"],
        "dates": "F",
        "ints": ["C", "D", "E"],
        "money": ["G"],
        "dv": {"H": "결제 상태"},
        "widths": {"A": 12, "B": 18, "C": 11, "D": 11, "E": 13, "F": 13, "G": 16, "H": 12},
        "note": {
            "A": _CLIENT_ID_NOTE,
            "G": "계약 및 결제 정보의 통화 기준입니다. 통화를 섞지 마세요.",
        },
    },
    {
        "title": "클레임 · 히스토리",
        "keep": True,
        "freeze_cols": 2,
        "headers": [
            "Client ID *", "고객사", "계약 차수", "클레임/히스토리 종류 *", "발생 날짜 *",
            "보상 종류", "조치 진행상황 *", "조치 날짜",
        ],
        "array": {"B": lookup_company()},
        "seed": [
            {"A": 2102, "C": 1, "D": "신기능 TEST (프리미어 플러그인)", "E": d(46234), "G": "접수"},
        ],
        "auto": ["B"],
        "dates": "EH",
        "ints": ["C"],
        "dv": {"G": "클레임 진행상황"},
        "widths": {"A": 12, "B": 18, "C": 11, "D": 26, "E": 13, "F": 18, "G": 14, "H": 13},
        "note": {
            "A": _CLIENT_ID_NOTE,
            "D": "불만뿐 아니라 협업·테스트 같은 특이사항도 여기 기록합니다.",
            "G": "'조치 완료'가 아닌 건은 목록 화면의 미처리 클레임 카드에 뜹니다.",
        },
    },
    {
        "title": "검증",
        "freeze_cols": 3,
        # 통째로 파생이다 — 입력할 칸이 하나도 없다. 계약 및 결제 정보의 행을 그대로 따라간다.
        "headers": [
            "Client ID", "계약 차수", "고객사", "계약 크레딧", "산정 크레딧", "누적 지급 크레딧",
            "계약 대비 차이", "총 계약금액", "분납 금액 합계", "금액 차이", "수금 완료액", "수금율", "확인",
        ],
        "array": {
            "A": _array(
                f"IF(ISNUMBER('{CONTRACTS_TAB}'!$A$2:$A),'{CONTRACTS_TAB}'!$A$2:$A,\"\")"
            ),
            "B": _array(
                f"IF(ISNUMBER('{CONTRACTS_TAB}'!$A$2:$A),'{CONTRACTS_TAB}'!$C$2:$C,\"\")"
            ),
            "C": lookup_company(),
            "D": sum_by_contract(CONTRACTS_TAB, "J"),
            # 산정 크레딧 = 공급가 ÷ 분당 단가 × 60. 통화가 다르면 계약 행에 박아 둔 환율로
            # 단가를 계약 통화로 옮긴다. 이 탭은 계약 탭의 행을 그대로 따라가므로 조회 없이
            # 같은 행을 위치로 가리키면 된다 — 복합키 VLOOKUP 을 다섯 번 할 이유가 없다.
            # 공급가나 단가가 없으면 계산하지 않는다. 0 으로 두면 "산정 0 ≠ 계약 크레딧" 이
            # 되어, 정보가 없을 뿐인 행이 전부 틀린 것처럼 붉게 뜬다.
            "E": _array(
                f"IF(ISNUMBER($A$2:$A)*ISNUMBER('{CONTRACTS_TAB}'!$M$2:$M)"
                f"*ISNUMBER('{CONTRACTS_TAB}'!$O$2:$O)*('{CONTRACTS_TAB}'!$O$2:$O>0),"
                f"IFERROR(ROUND('{CONTRACTS_TAB}'!$M$2:$M/"
                f"IF('{CONTRACTS_TAB}'!$N$2:$N='{CONTRACTS_TAB}'!$K$2:$K,"
                f"'{CONTRACTS_TAB}'!$O$2:$O,"
                f"IF('{CONTRACTS_TAB}'!$N$2:$N=\"USD\","
                f"'{CONTRACTS_TAB}'!$O$2:$O*'{CONTRACTS_TAB}'!$P$2:$P,"
                f"'{CONTRACTS_TAB}'!$O$2:$O/'{CONTRACTS_TAB}'!$P$2:$P))*60,0),\"\"),\"\")"
            ),
            "F": sum_by_contract(CREDITS_TAB, "G"),
            "G": _array('IF(ISNUMBER($A$2:$A),$D$2:$D-$F$2:$F,"")'),
            "H": sum_by_contract(CONTRACTS_TAB, "L"),
            "I": sum_by_contract(PAYMENTS_TAB, "G"),
            "J": _array('IF(ISNUMBER($A$2:$A),$H$2:$H-$I$2:$I,"")'),
            "K": _array(
                f'IF(ISNUMBER($A$2:$A),SUMIF({_pair(PAYMENTS_TAB, "A", "C")}'
                f'&"|"&\'{PAYMENTS_TAB}\'!$H$2:$H,$A$2:$A&"|"&$B$2:$B&"|입금 완료",'
                f"'{PAYMENTS_TAB}'!$G$2:$G),\"\")"
            ),
            "L": _array('IF(ISNUMBER($A$2:$A)*($H$2:$H>0),$K$2:$K/$H$2:$H,"")'),
            "M": _array(
                # 금액도 ±1 은 봐준다. 회차 금액을 반올림하면 총액과 1 차이가 나는데,
                # 그걸 불일치로 띄우면 진짜 어긋난 행이 그 사이에 묻힌다.
                'IF(ISNUMBER($A$2:$A),IF((ABS($J$2:$J)<=1)*($G$2:$G=0)*'
                '(($E$2:$E="")+(ABS($D$2:$D-$E$2:$E)<=1)),"OK",'
                'TRIM(IF(ABS($J$2:$J)>1,"금액 불일치 ","")&IF($G$2:$G>0,"크레딧 미지급 ","")'
                '&IF($G$2:$G<0,"계약 외 추가지급 ","")'
                '&IF(($E$2:$E<>"")*(ABS($D$2:$D-$E$2:$E)>1),"크레딧 산정 불일치",""))),"")'
            ),
        },
        "seed": [],
        "auto": list("ABCDEFGHIJKLM"),
        "money": ["D", "E", "F", "G", "H", "I", "J", "K"],
        "ints": ["B"],
        "percent": ["L"],
        "flag": "M",
        "widths": {"A": 12, "B": 10, "C": 18, "D": 13, "E": 13, "F": 15, "G": 13, "H": 15,
                   "I": 15, "J": 13, "K": 15, "L": 10, "M": 22},
        "note": {
            "A": "입력할 칸이 없습니다. 계약 및 결제 정보의 행을 그대로 따라갑니다.",
            "E": "공급가 ÷ 분당 단가 × 60 을 시트가 다시 계산한 값입니다. 계약 크레딧과 "
                 "다르면 둘 중 하나가 틀린 것입니다.",
            "G": "계약 크레딧 − 누적 지급 크레딧. 음수면 계약 외 추가 지급이 있었다는 뜻입니다.",
            "J": "총 계약금액 − 분납 금액 합계. 0이 아니면 분납 설계가 총액과 맞지 않습니다.",
        },
    },
]

HEADER_BG = {"red": 0.85, "green": 0.89, "blue": 0.94}
AUTO_BG = {"red": 0.949, "green": 0.957, "blue": 0.965}
FLAG_BG = {"red": 0.99, "green": 0.84, "blue": 0.83}
BAND_BG = {"red": 0.976, "green": 0.980, "blue": 0.988}


# 회사 명단을 만들어 올 수 있는 탭들. (탭, 첫 데이터 행, {명단 열: 그 탭의 0-기준 열})
# 문의 날짜가 곧 최초 연락일이다. Outbound DB 도 여기 있었지만 탭째 지웠다 — 계약이 있는
# 네 곳(2002·2003·2094·2101)은 명단에 남아 있고, 나머지 아웃바운드 리드는 관리하지 않는다.
_COMPANY_SOURCES = (
    (INBOUND, INBOUND_FIRST_ROW, {"C": 5, "D": 6, "E": 8, "F": 7, "H": 1}),
)


def companies_from_history(sheets) -> dict[int, dict]:
    """Inbound DB 를 Client ID 로 묶어 회사 명단으로. ``--import-inbound`` 전용.

    문의는 회사당 여러 번 오지만 Client ID 는 회사당 하나다(agents/client_ids.py). 그래서
    묶으면 그대로 회사 명단이 된다. 최초 연락일은 그 회사의 가장 이른 날짜다.

    **평소에는 돌지 않는다.** Inbound DB 의 고객사 열이 이제 이 탭을 조회하므로, 매번 여기서
    다시 만들면 값의 출처가 자기 자신이 되어 한 번 비는 순간 영영 빈다.
    """
    found: dict[int, dict] = {}
    for tab, first_row, columns in _COMPANY_SOURCES:
        try:
            rows = (
                sheets.values()
                .get(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"'{tab}'!A{first_row}:Z",
                    # 원시 수식으로 읽는다. Inbound DB 의 고객사 칸은 이제 이 명단을
                    # 조회하므로, 계산된 값을 읽으면 명단이 명단을 베끼게 된다 —
                    # 한 번 비는 순간 영영 빈다. "=" 로 시작하는 칸은 출처가 아니다.
                    valueRenderOption="FORMULA",
                )
                .execute()
                .get("values")
                or []
            )
        except Exception:
            print(f"  '{tab}' 을 읽지 못했습니다 — 건너뜁니다.")
            continue
        width = max(columns.values()) + 1
        for line in rows:
            line = list(line) + [""] * (width - len(line))
            raw_id = str(line[0]).replace(",", "").strip()
            if not raw_id.isdigit():
                continue
            client_id = int(raw_id)
            entry = found.setdefault(client_id, {"A": client_id, "G": department_of(client_id)})
            for column, source in columns.items():
                value = str(line[source]).strip()
                if not value or value.startswith("="):
                    continue
                if column == "H":  # 최초 연락일은 가장 이른 날짜
                    if len(value) == 10 and value[4] == "-" and (
                        not entry.get("H") or value < entry["H"]
                    ):
                        entry["H"] = value
                elif not entry.get(column):
                    entry[column] = value
        print(f"  '{tab}' 에서 회사 {len(found)}곳까지 모았습니다.")
    return found


def point_inbound_at_registry(sheets) -> None:
    """Inbound DB 의 고객사·국가·기업 종류를 회사 명단 조회로 (다시) 쓴다.

    빌드 끝에 항상 돈다. 명단 탭을 지웠다 다시 만들면 그걸 가리키던 수식이 끊길 수 있는데,
    끊긴 것을 눈으로 확인할 방법이 없다 — 그냥 이름이 빈 채로 있을 뿐이다. 그래서 매번
    새로 쓴다. 앞으로 들어오는 행은 append 경로가 같은 수식을 넣는다
    (`google_sheets._write_registry_formulas`).
    """
    rows = (
        sheets.values()
        .get(spreadsheetId=SPREADSHEET_ID, range=f"'{INBOUND}'!A{INBOUND_FIRST_ROW}:A")
        .execute()
        .get("values")
        or []
    )
    data = []
    for offset, line in enumerate(rows):
        row = INBOUND_FIRST_ROW + offset
        if not (line and str(line[0]).replace(",", "").strip().isdigit()):
            continue
        # F 고객사 ← 명단 C(3) / H 국가 ← 명단 F(6) / I 기업 종류 ← 명단 E(5)
        for letter, column in (("F", 3), ("H", 6), ("I", 5)):
            data.append(
                {
                    "range": f"'{INBOUND}'!{letter}{row}",
                    "values": [
                        [
                            f"=IFERROR(VLOOKUP($A{row},'{CLIENTS_TAB}'!$A:$J,"
                            f'{column},FALSE),"")'
                        ]
                    ],
                }
            )
    if not data:
        return
    sheets.values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
    print(f"'{INBOUND}' 의 고객사·국가·기업 종류 {len(data)}칸을 명단 조회로 맞췄습니다.")


# 우리 탭이 대체한 옛 탭들. **지우지 않고 숨긴다** — Subscriptions 의 갱신 여부·담당,
# Payments 의 결제 주기처럼 다른 데 없는 칸이 몇 개 있어서, 수주 DB 33건을 콘솔로 옮기기
# 전에 지우면 그것들이 같이 사라진다. 숨기면 눈앞에서는 치워지고 되돌리기는 한 번이다.
_RETIRED: dict[str, str] = {}  # 대체만 하고 남겨 둘 탭은 이제 없다
# 내용을 전부 새 탭으로 옮겼거나, 애초에 사본이라 남길 이유가 없는 것들.
_JUNK = (
    "Sheet18",             # 수주 DB 한 행짜리 사본
    "Perso 계정 및 플랜",     # 계약 및 결제 정보에 합쳤다
    "소통 히스토리",         # 오간 내용은 HubSpot 타임라인에 남긴다
    "수주 DB",              # 33건을 계약·회차·클레임으로 나눠 넣었다
    "Plan Setting",        # 한도 5열이 전부 계약 탭에 있다
    "Payments",            # 분납 스케줄을 결제 현황으로 옮겼다
    "Subscriptions",       # 담당·갱신 여부·사용 중단 이유를 계약 탭으로 옮겼다
    "선택지",              # 드롭다운이 목록을 규칙 안에 들고 있다
)


def tidy_workbook(sheets) -> None:
    """대체된 탭은 숨기고, 남은 조각은 지우고, 우리 탭을 앞으로 옮긴다."""
    meta = sheets.get(spreadsheetId=SPREADSHEET_ID, fields="sheets.properties").execute()
    props = {s["properties"]["title"]: s["properties"] for s in meta["sheets"]}
    requests = []
    for title in _JUNK:
        if title in props:
            requests.append({"deleteSheet": {"sheetId": props[title]["sheetId"]}})
            print(f"  '{title}' 을 지웠습니다 — 내용은 새 탭으로 옮겼습니다.")
    for title, why in _RETIRED.items():
        if title in props and not props[title].get("hidden"):
            requests.append(
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": props[title]["sheetId"], "hidden": True},
                        "fields": "hidden",
                    }
                }
            )
            print(f"  '{title}' 을 숨겼습니다 — {why}")
    # 원본 → 계약 → 회차 → 이벤트 → 검산 → 이력 순으로. 읽는 순서가 곧 데이터의 순서다.
    order = [tab["title"] for tab in TABS if not tab.get("hidden")] + [INBOUND]
    for position, title in enumerate(order):
        if title in props:
            requests.append(
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": props[title]["sheetId"], "index": position},
                        "fields": "index",
                    }
                }
            )
    if requests:
        sheets.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": requests}).execute()
        print("탭을 정리했습니다.")


def existing_rows(sheets, tab: dict) -> list[dict]:
    """다시 지을 때 살려 낼 내용. 수식 칸은 빼고 값만 가져온다."""
    width = len(tab["headers"])
    values = (
        sheets.values()
        .get(spreadsheetId=SPREADSHEET_ID, range=f"'{tab['title']}'!A2:{col(width - 1)}")
        .execute()
        .get("values")
        or []
    )
    derived = set(tab.get("array") or {})
    saved = []
    for line in values:
        row = {
            col(i): value
            for i, value in enumerate(line)
            if col(i) not in derived and str(value).strip()
        }
        if row.get("A"):
            saved.append(row)
    return saved


def build() -> None:
    replace = "--replace" in sys.argv
    reimport = "--import-inbound" in sys.argv
    service = gs._build_service()
    sheets = service.spreadsheets()
    meta = sheets.get(spreadsheetId=SPREADSHEET_ID).execute()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    wanted = [t["title"] for t in TABS]

    clash = [t for t in wanted if t in existing]
    if clash and not replace:
        raise SystemExit(f"이미 있는 탭: {', '.join(clash)} — 다시 만들려면 --replace")

    # 지우기 전에 살릴 것을 먼저 읽는다.
    for tab in TABS:
        if tab.get("keep") and tab["title"] in existing:
            tab["_kept"] = existing_rows(sheets, tab)
            print(f"  '{tab['title']}': 기존 {len(tab['_kept'])}행을 살려 둡니다.")

    if clash:
        sheets.batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"deleteSheet": {"sheetId": existing[t]}} for t in clash]},
        ).execute()
        print(f"지웠습니다: {', '.join(clash)}")

    created = sheets.batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "requests": [
                {
                    "addSheet": {
                        "properties": {
                            "title": tab["title"],
                            "hidden": tab.get("hidden", False),
                            "gridProperties": {
                                "rowCount": GRID_ROWS,
                                "columnCount": max(
                                    len(tab.get("headers") or []),
                                    max((len(r) for r in tab.get("plain") or [[]]), default=1),
                                ),
                                "frozenRowCount": 1,
                                # Client ID 와 고객사까지 얼려 둔다. 오른쪽으로 스크롤해도
                                # 이 행이 누구 것인지가 화면에서 안 사라진다.
                                "frozenColumnCount": tab.get("freeze_cols", 0),
                            },
                        }
                    }
                }
                for tab in TABS
            ]
        },
    ).execute()
    ids = {
        r["addSheet"]["properties"]["title"]: r["addSheet"]["properties"]["sheetId"]
        for r in created["replies"]
    }
    print("만들었습니다:", ", ".join(ids))

    # ---- 값과 수식 -------------------------------------------------------
    data: list[dict] = []
    raw_data: list[dict] = []
    for tab in TABS:
        title = tab["title"]
        if tab.get("plain") is not None:
            data.append({"range": f"'{title}'!A1", "values": tab["plain"]})
            continue

        headers = tab["headers"]
        width = len(headers)
        data.append({"range": f"'{title}'!A1", "values": [headers]})

        rows = list(tab.get("_kept") or [])
        if reimport and tab.get("keep"):
            # Client ID 로 합치는 것은 회사 명단뿐이다. 회차 탭에 같은 짓을 하면 한 계약의
            # 2회차가 1회차에 덮여 사라진다.
            #
            # **빈칸만 채운다.** 이미 명단에 있는 값을 덮어쓰면 운영자가 고쳐 둔 이름이
            # 문의 기록의 옛 철자로 되돌아가고, 원본이 어느 쪽인지가 매번 뒤집힌다.
            merged = {
                int(row["A"]): dict(row)
                for row in rows
                if str(row.get("A", "")).strip().isdigit()
            }
            for client_id, entry in companies_from_history(sheets).items():
                target = merged.setdefault(client_id, {"A": client_id})
                for column, value in entry.items():
                    if value and not str(target.get(column, "")).strip():
                        target[column] = value
            for row in tab.get("seed") or []:
                merged.setdefault(row["A"], {"A": row["A"]}).update(row)
            rows = [merged[client_id] for client_id in sorted(merged)]
            print(f"  '{title}': 회사 {len(merged)}곳 (기존 {len(tab.get('_kept') or [])}곳 유지)")
        elif not rows:
            rows = list(tab.get("seed") or [])

        derived = tab.get("array") or {}
        grid = []
        for row in rows:
            line = [""] * width
            for letter, value in row.items():
                if idx(letter) < width and letter not in derived:
                    line[idx(letter)] = value
            grid.append(line)
        if grid:
            data.append({"range": f"'{title}'!A2", "values": grid})
        # 수식은 값 위에 덮어쓴다 — 2행 한 칸이 열 전체를 맡는다.
        for letter, formula in derived.items():
            data.append({"range": f"'{title}'!{letter}2", "values": [[formula]]})
        for cell, value in (tab.get("raw") or {}).items():
            raw_data.append({"range": f"'{title}'!{cell}", "values": [[value]]})

    sheets.values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
    if raw_data:
        sheets.values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "RAW", "data": raw_data},
        ).execute()
    print("값·수식을 넣었습니다.")

    # ---- 서식 · 드롭다운 · 보호 -------------------------------------------
    requests: list[dict] = []

    def whole(sheet_id: int, letter: str) -> dict:
        return {
            "sheetId": sheet_id,
            "startRowIndex": 1,
            "startColumnIndex": idx(letter),
            "endColumnIndex": idx(letter) + 1,
        }

    def fmt(sheet_id: int, letters, pattern: str, kind: str) -> None:
        for letter in letters:
            requests.append(
                {
                    "repeatCell": {
                        "range": whole(sheet_id, letter),
                        "cell": {
                            "userEnteredFormat": {
                                "numberFormat": {"type": kind, "pattern": pattern}
                            }
                        },
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
            )

    for tab in TABS:
        sheet_id = ids[tab["title"]]
        for letter, chars in (tab.get("widths") or {}).items():
            requests.append(
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": idx(letter),
                            "endIndex": idx(letter) + 1,
                        },
                        "properties": {"pixelSize": int(chars * 8)},
                        "fields": "pixelSize",
                    }
                }
            )
        if tab.get("plain") is not None:
            requests.append(
                {
                    "repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold",
                    }
                }
            )
            continue

        width = len(tab["headers"])
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": width,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": HEADER_BG,
                            "textFormat": {"bold": True},
                            "wrapStrategy": "WRAP",
                            "verticalAlignment": "MIDDLE",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)",
                }
            }
        )
        # 필터. 264곳을 눈으로 훑을 수는 없다 — 열 제목의 깔때기에서 Client ID 를 치면
        # 그 행만 남는다. 값을 바꾸지 않으므로 배열 수식과도 부딪히지 않는다.
        requests.append(
            {
                "setBasicFilter": {
                    "filter": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "startColumnIndex": 0,
                            "endColumnIndex": width,
                        }
                    }
                }
            }
        )
        # 줄무늬. 표가 넓어서 눈이 행을 놓친다.
        requests.append(
            {
                "addBanding": {
                    "bandedRange": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "startColumnIndex": 0,
                            "endColumnIndex": width,
                        },
                        "rowProperties": {
                            "headerColor": HEADER_BG,
                            "firstBandColor": {"red": 1, "green": 1, "blue": 1},
                            "secondBandColor": BAND_BG,
                        },
                    }
                }
            }
        )
        for letter, note in (tab.get("note") or {}).items():
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": idx(letter),
                            "endColumnIndex": idx(letter) + 1,
                        },
                        "cell": {"note": note},
                        "fields": "note",
                    }
                }
            )
        for letter in tab.get("auto") or []:
            requests.append(
                {
                    "repeatCell": {
                        "range": whole(sheet_id, letter),
                        "cell": {"userEnteredFormat": {"backgroundColor": AUTO_BG}},
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                }
            )
        # 수식 열 보호. 경고만 띄운다 — 막아 버리면 정말 고쳐야 할 때 손이 묶인다.
        for letter in tab.get("array") or {}:
            requests.append(
                {
                    "addProtectedRange": {
                        "protectedRange": {
                            "range": whole(sheet_id, letter),
                            "description": f"{tab['title']} {letter}열 — 수식",
                            "warningOnly": True,
                        }
                    }
                }
            )
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": idx(letter),
                            "endColumnIndex": idx(letter) + 1,
                        },
                        "cell": {"note": (tab.get("note") or {}).get(letter) or _FORMULA_NOTE},
                        "fields": "note",
                    }
                }
            )
        fmt(sheet_id, tab.get("dates") or "", "yyyy-mm-dd", "DATE")
        fmt(sheet_id, tab.get("money") or [], "#,##0", "NUMBER")
        fmt(sheet_id, tab.get("ints") or [], "0", "NUMBER")
        fmt(sheet_id, tab.get("percent") or [], "0.0%", "PERCENT")
        fmt(sheet_id, tab.get("text") or [], "@", "TEXT")

        for letter, name in (tab.get("dv") or {}).items():
            requests.append(
                {
                    "setDataValidation": {
                        "range": whole(sheet_id, letter),
                        "rule": {
                            # 값을 규칙 안에 넣는다 — 참조할 탭이 없으니 지워질 일도 없다.
                            "condition": {
                                "type": "ONE_OF_LIST",
                                "values": [{"userEnteredValue": v} for v in choices(name)],
                            },
                            "showCustomUi": True,
                            "strict": False,
                        },
                    }
                }
            )
        if tab.get("flag"):
            letter = tab["flag"]
            requests.append(
                {
                    "addConditionalFormatRule": {
                        "rule": {
                            "ranges": [whole(sheet_id, letter)],
                            "booleanRule": {
                                "condition": {
                                    "type": "CUSTOM_FORMULA",
                                    "values": [
                                        {"userEnteredValue": f'=AND($A2<>"",${letter}2<>"OK")'}
                                    ],
                                },
                                "format": {"backgroundColor": FLAG_BG},
                            },
                        },
                        "index": 0,
                    }
                }
            )

    sheets.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": requests}).execute()
    print("서식·드롭다운·보호를 넣었습니다.")
    point_inbound_at_registry(sheets)
    tidy_workbook(sheets)
    print(f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit")


def refresh_derived() -> None:
    """이미 쓰고 있는 워크북의 **수식과 드롭다운만** 다시 씁니다. 데이터는 안 건드립니다.

    파생 열은 시트 안의 ARRAYFORMULA 이고 드롭다운은 규칙 안에 값을 들고 있어서, 파이썬
    쪽 표를 고쳐도 살아 있는 시트는 예전 값을 그대로 씁니다 — 고객 종류를 Inbound 에서
    GTM Inbound 로 바꿨을 때 실제로 그랬습니다. ``--replace`` 는 탭을 지우고 다시 만드는
    것이라 손으로 채운 칸까지 날아갑니다.

    열당 한 칸(2행)만 쓰면 열 전체가 다시 계산되므로, 기존 행도 그 자리에서 같이 바뀝니다.
    """
    service = gs._build_service()
    sheets = service.spreadsheets()
    meta = sheets.get(spreadsheetId=SPREADSHEET_ID).execute()
    ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    data, requests = [], []
    for tab in TABS:
        title = tab["title"]
        if title not in ids:
            print(f"  '{title}': 워크북에 없어 건너뜁니다.")
            continue
        for letter, formula in (tab.get("array") or {}).items():
            data.append({"range": f"'{title}'!{letter}2", "values": [[formula]]})
        for letter, name in (tab.get("dv") or {}).items():
            requests.append({
                "setDataValidation": {
                    "range": {
                        "sheetId": ids[title], "startRowIndex": 1,
                        "startColumnIndex": idx(letter), "endColumnIndex": idx(letter) + 1,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": v} for v in choices(name)],
                        },
                        "showCustomUi": True, "strict": False,
                    },
                }
            })

    if data:
        sheets.values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()
    if requests:
        sheets.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": requests}).execute()
    print(f"수식 {len(data)}칸 · 드롭다운 {len(requests)}열을 다시 썼습니다.")
    print(f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit")


if __name__ == "__main__":
    if "--refresh-derived" in sys.argv:
        refresh_derived()
    else:
        build()
