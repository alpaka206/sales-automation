"""Google Sheets synchronization that preserves the sales team's workbook schema."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from ..common.config import settings

logger = logging.getLogger(__name__)
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_APPEND_LOCK = threading.Lock()


@dataclass(frozen=True)
class SheetWriteResult:
    row: int | None = None
    client_id: int | None = None


_ALIASES: dict[str, tuple[str, ...]] = {
    # Inbound DB
    "client_id": ("Cluent ID", "Client ID", "client_id"),
    "inquiry_key": ("Inquiry ID", "문의 ID", "Ticket ID", "inquiry_key"),
    "sales_direction": ("영업방향",),
    "inquiry_date": ("문의 날짜", "Create Date"),
    # "Ticket Status"/"Deal Detail" are the rebuilt workbook's names for these two.
    # update_inbound_stage writes _STAGE_VALUES into exactly this pair, so a wrong
    # alias here silently overwrites a different column of the sales team's sheet.
    "deal_stage": ("Deal Stage", "Ticket Status"),
    "deal_stage_detail": ("Deal Stage Detail", "Deal Detail"),
    "pipeline": ("Pipeline",),
    # The workbook was rebuilt with English column names in 2026; both spellings are
    # listed so an older copy of the sheet keeps working.
    "company": ("고객사", "회사", "회사명", "Company Name"),
    "full_name": ("고객사 담당자", "담당자", "성함", "이름", "Full Name"),
    "phone": ("Contact Phone #", "전화번호", "연락처", "Phone Number"),
    "email": ("Contact Email", "Billing Email", "이메일", "메일", "Email"),
    "country": ("Country", "국가", "IP Country"),
    "company_type": ("기업 종류", "산업군"),
    "channel": ("소통 채널", "채널"),
    "plan": ("구독 플랜", "플랜", "Plan"),
    # Normalisation strips whitespace but keeps underscores, so "User_Seq" and
    # "user seq" are two distinct spellings and both have to be listed.
    "user_seq": ("User_Seq", "user-seq", "user seq"),
    "source": ("Perso AI 알게된 경로", "유입 소스", "Perso Dubbing 알게된 경로"),
    "history": ("문의 히스토리", "문의 내용"),
    "first_meeting_at": ("초도 미팅 날짜",),
    "proposal_call": ("제안 통화",),
    "amount": ("제안가", "총 계약 금액 (VAT포함)", "계약 금액"),
    "amount_usd": ("제안가 USD",),
    "notes": ("제안가 비고", "계약 비고", "비고", "메모", "제안 비고"),
    "credits": ("제안 크레딧", "계약 크레딧"),
    "inquiry_month": ("문의 월(YYYY-MM)",),
    "inquiry_quarter": ("문의 분기(YYYY-Qn)",),
    # 수주 DB
    "department": ("담당부서",),
    "customer_classification": ("고객분류",),
    "order_date": ("수주일",),
    "contract_method": ("계약서",),
    "payment_instrument": ("결제 수단",),
    "first_payment_date": ("최초 결제일",),
    "account_status": ("계정 활성 상태",),
    "payment_method": ("결제 방식",),
    "billing_email": ("Billing Email",),
    "contract_months": ("계약기간(개월)",),
    "currency": ("통화",),
    "owner_email": ("Perso Email (Owner Email)",),
    "space_seq": ("space_seq",),
    "plan_start_date": ("플랜 시작일",),
    "enterprise_name": ("Enterprize Name", "Enterprise Name"),
    "invitation_limit": ("Account Invitation Limit",),
    "queue_limit": ("Que limit", "Queue limit"),
    "concurrent_jobs": ("Concurrent Jobs",),
    "space_count": ("Space 개수",),
    "credit_history": ("크레딧 제공 히스토리",),
    "payer": ("지급자",),
    "plan_notes": ("플랜 비고",),
    "claim_type": ("고객 클레임 종류",),
    "compensation_type": ("보상 종류",),
    "action_date": ("조치 날짜",),
    "history_detail": ("히스토리 디테일",),
    "payment_month": ("결제 월(YYYY-MM)",),
    "payment_quarter": ("결제 분기(YYYY-Qn)",),
}


class GoogleSheetsError(RuntimeError):
    pass


def is_configured() -> bool:
    """Sheets sync is user-OAuth-only (org policy blocks service-account sharing)."""
    from .google_oauth import load_grant

    try:
        has_user_grant = load_grant() is not None
    except Exception:
        logger.warning("Stored Google Sheets OAuth grant is unavailable.", exc_info=True)
        has_user_grant = False
    return bool(settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip() and has_user_grant)


def writes_enabled() -> bool:
    """Writes are automatic once credentials exist; no duplicate feature flag.

    Pre-launch safe mode disables ALL Sheet writes (append + stage update) so test
    rows never pollute the shared sales workbook. Reads are unaffected.

    ``live_sheets_writes`` is LIVE_EXTERNAL_WRITES and LIVE_SHEETS_WRITES together,
    so the workbook can be held read-only after go-live without blocking HubSpot.
    """
    from ..common.safe_mode import live_sheets_writes

    return is_configured() and live_sheets_writes()


def _build_service():
    try:
        from google.oauth2 import credentials as user_credentials
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover
        raise GoogleSheetsError("Install google-api-python-client and google-auth.") from exc

    from .google_oauth import TOKEN_URL, client_id, client_secret, load_grant

    grant = load_grant()
    if grant is None:
        raise GoogleSheetsError(
            "Google Sheets is not connected — complete the OAuth sign-in first."
        )
    payload, _account_email = grant
    expiry = datetime.fromtimestamp(
        int(payload.get("expires_at") or 0), tz=timezone.utc
    ).replace(tzinfo=None)
    credentials = user_credentials.Credentials(
        token=payload.get("access_token"),
        refresh_token=payload.get("refresh_token"),
        token_uri=TOKEN_URL,
        client_id=client_id(),
        client_secret=client_secret(),
        scopes=payload.get("scopes") or _SCOPES,
        expiry=expiry,
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _normalise_header(value: object) -> str:
    return "".join(str(value or "").strip().lower().split())


def _header_lookup(record: dict) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for key in record:
        aliases = set(_ALIASES.get(key, (key,)))
        for alias in aliases:
            if alias:
                lookup[_normalise_header(alias)] = key
    return lookup


def _key_for_header(header: object, lookup: dict[str, str]) -> str | None:
    normal = _normalise_header(header)
    key = lookup.get(normal)
    # 수주 DB A1 contains the ID rule and "Client ID" in the same wrapped cell.
    if key is None and normal.endswith("clientid") and "client_id" in lookup.values():
        return "client_id"
    return key


def _mapped_row(headers: list[object], record: dict) -> list[object]:
    lookup = _header_lookup(record)
    matched = 0
    row: list[object] = []
    for header in headers:
        key = _key_for_header(header, lookup)
        if key:
            matched += 1
        value = record.get(key, "") if key else ""
        row.append(value if value is not None else "")
    if matched < 2:
        raise GoogleSheetsError("Existing sheet headers did not match enough known fields.")
    return row


def _record_from_row(headers: list[object], row: list[object]) -> dict:
    lookup = _header_lookup(_ALIASES)
    record: dict[str, object] = {}
    for index, header in enumerate(headers):
        key = _key_for_header(header, lookup)
        if key and index < len(row):
            record[key] = row[index]
    return record


def _column_letter(index: int) -> str:
    result = ""
    while index >= 0:
        index, remainder = divmod(index, 26)
        result = chr(65 + remainder) + result
        index -= 1
    return result


# How many leading rows to scan for the real header. The sales workbook puts a merged
# group-label row above Inbound DB's header ("고객사", "고객사 담당자" spanning several
# columns) and an ID-rule note above 수주 DB's, so row 1 is not the header there.
_HEADER_SCAN_ROWS = 5


@dataclass(frozen=True)
class _SheetHeader:
    """A tab's header row and where it sits, so data ranges follow it.

    Row 1 used to be hardcoded in five places. On a workbook whose header is row 2
    that made every read return the label row as a record and every row number off by
    one — silently, because the values still parse.
    """

    values: list[object]
    row: int  # 1-based, as Sheets counts

    @property
    def first_data_row(self) -> int:
        return self.row + 1

    @property
    def last_column(self) -> str:
        return _column_letter(len(self.values) - 1)


def _headers(service, tab: str) -> _SheetHeader:
    """Locate the header: the first row carrying a recognisable Client ID column.

    Client ID is the workbook's join key — every tab this app touches is keyed on it,
    and the readers already refuse a tab without one — so its presence identifies the
    header far more reliably than "the first non-empty row", which is exactly what a
    merged group-label row would win.
    """
    values = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
            range=f"'{tab}'!1:{_HEADER_SCAN_ROWS}",
        )
        .execute()
        .get("values")
        or []
    )
    lookup = _header_lookup({"client_id": ""})
    for offset, row in enumerate(values, start=1):
        if any(_key_for_header(cell, lookup) == "client_id" for cell in row):
            return _SheetHeader(values=list(row), row=offset)
    # No Client ID anywhere in the scanned rows. Fall back to the first non-empty row
    # so callers report the real problem ("no Client ID column") rather than a
    # misleading "no header row".
    for offset, row in enumerate(values, start=1):
        if row:
            return _SheetHeader(values=list(row), row=offset)
    raise GoogleSheetsError(f"'{tab}' tab has no header row; refusing to modify it.")


def _ensure_inquiry_key_column(service, tab: str, header: _SheetHeader) -> _SheetHeader:
    """Add the per-inquiry idempotency column at the far right when first needed.

    Appending at the right edge does not shift the operator's existing columns or
    formulas. Old rows may stay blank; stage/delete operations fall back to Client ID
    only while that ID still identifies exactly one legacy row.
    """
    lookup = _header_lookup({"inquiry_key": ""})
    if any(_key_for_header(cell, lookup) == "inquiry_key" for cell in header.values):
        return header
    index = len(header.values)
    service.spreadsheets().values().update(
        spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
        range=f"'{tab}'!{_column_letter(index)}{header.row}",
        valueInputOption="RAW",
        body={"values": [["Inquiry ID"]]},
    ).execute()
    logger.info("Added Inquiry ID column to '%s' at %s.", tab, _column_letter(index))
    return _SheetHeader(values=[*header.values, "Inquiry ID"], row=header.row)


def _backfill_legacy_inquiry_key(
    service,
    tab: str,
    header: _SheetHeader,
    client_id: int,
    inquiry_key: str,
) -> None:
    """Give a pre-0085 row its key before another row shares the Client ID."""
    if _rows_for_value(service, tab, header, "inquiry_key", inquiry_key):
        return
    rows = _rows_for_value(service, tab, header, "client_id", client_id)
    if len(rows) != 1:
        raise GoogleSheetsError(
            f"Cannot backfill Inquiry ID for Client ID {client_id}: found {len(rows)} rows."
        )
    lookup = _header_lookup({"inquiry_key": ""})
    index = next(
        idx
        for idx, cell in enumerate(header.values)
        if _key_for_header(cell, lookup) == "inquiry_key"
    )
    service.spreadsheets().values().update(
        spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
        range=f"'{tab}'!{_column_letter(index)}{rows[0]}",
        valueInputOption="RAW",
        body={"values": [[inquiry_key]]},
    ).execute()


def _next_inbound_client_id(service, tab: str, header: _SheetHeader) -> int:
    lookup = _header_lookup({"client_id": ""})
    index = next(
        (idx for idx, cell in enumerate(header.values) if _key_for_header(cell, lookup) == "client_id"),
        None,
    )
    if index is None:
        raise GoogleSheetsError("Inbound DB has no Client ID column.")
    column = _column_letter(index)
    values = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
            range=f"'{tab}'!{column}{header.first_data_row}:{column}",
        )
        .execute()
        .get("values")
        or []
    )
    ids = []
    for row in values:
        try:
            value = int(str(row[0]).replace(",", ""))
        except (IndexError, TypeError, ValueError):
            continue
        if 1000 <= value < 2000:
            ids.append(value)
    next_id = max(ids, default=999) + 1
    if next_id >= 2000:
        raise GoogleSheetsError("Inbound Client ID 1000-series is exhausted.")
    return next_id


# Pipeline (E on the current sheet) is a FORMULA, not a value the app decides once and
# forgets. The sales team edits 구독 플랜 by hand as a deal moves, and a written-in
# "MQL" would then contradict the plan sitting beside it — the formula re-reads that cell
# forever. The operator's rule, 2026-09-02:
#
#     빈칸 / N/A / Free / 무료 …  -> MQL      (아직 아무것도 안 샀다)
#     그 외 플랜                   -> PQL      (엔터프라이즈 포함)
#
# **가지는 둘뿐입니다.** 예전에는 `엔터프라이즈 -> 재계약` 이 하나 더 있었는데, 그러면 같은
# 고객을 콘솔은 PQL 시트는 재계약이라고 불렀습니다 — 두 화면을 나란히 놓기 전에는 안 보이는
# 어긋남입니다. 그리고 **재계약인지는 이 콘솔이 더 정확하게 압니다**: 그 Client ID 아래
# 계약이 이미 있으면 재계약이고(수주 고객), 그건 플랜 이름으로 짐작할 일이 아닙니다.
#
# **「아직 아무것도 안 샀다」의 철자는 `sheet_values.PLAN_AS_NOT_APPLICABLE` 한 곳에서
# 옵니다.** 콘솔 화면도 같은 목록으로 MQL/PQL 을 정하므로(`qualification_for_plan`), 목록이
# 둘이면 같은 고객을 한쪽은 MQL 다른 쪽은 PQL 이라고 부릅니다.
#
# **빈칸 가지가 생긴 이유** (2026-09-02 운영자 지시): 「이 앱이 플랜 칸을 늘 채우므로 빈칸은
# 생길 수 없다」가 사실이 아니었습니다. `record_inbound` 경로만 `normalise_plan` 을 지나고,
# 허브스팟 연락처 동기화와 콘솔의 플랜 폼은 값을 **그대로** 씁니다 — 허브스팟에 `Free` 라고
# 적힌 고객이 시트에 `Free` 로 들어가 PQL 로 읽혔습니다. 사람이 손으로 비운 칸도 같습니다.
# 수식이 그 세 철자를 다 받아 주면 어느 경로로 들어왔든 답이 같습니다.
#
# 비교는 Sheets 의 `=` 이라 대소문자를 안 가립니다 — `Free` 도 `free` 도 같이 걸립니다.
#
# The plan column is located by header rather than hardcoded as N, so inserting a column
# does not silently point the formula at the wrong one.
def _pipeline_formula(header: _SheetHeader, row: int) -> str | None:
    from ..common.sheet_values import PLAN_AS_NOT_APPLICABLE

    lookup = _header_lookup({"plan": ""})
    plan_index = next(
        (i for i, cell in enumerate(header.values) if _key_for_header(cell, lookup) == "plan"),
        None,
    )
    if plan_index is None:
        return None
    cell = f"{_column_letter(plan_index)}{row}"
    # 빈칸이 먼저입니다 — 목록은 정렬해서 붙입니다. 세트의 순서는 실행마다 달라지므로,
    # 그대로 쓰면 같은 행에 같은 뜻의 수식이 매번 다른 글자로 다시 써집니다.
    nothing_bought = ",".join(
        f'{cell}="{word}"' for word in ("", *sorted(PLAN_AS_NOT_APPLICABLE))
    )
    return f'=IF(OR({nothing_bought}),"MQL","PQL")'


# 고객사·기업 종류·국가는 「고객 기본 정보」가 원본이다. 문의 행마다 값을 다시 적으면 같은
# 회사가 세 번 문의했을 때 서울대학교 / 서울대 / SNU 로 갈라지고, 고쳐도 한 행만 고쳐진다.
# 그래서 이 세 칸은 Client ID 로 그 탭을 조회한다 — 거기서 한 번 고치면 전부 따라 바뀐다.
#
# ARRAYFORMULA 를 쓰지 않는 이유: 이 탭은 앱이 행을 append 하는데, 배열이 채워야 할 자리에
# 값이 들어오면 열 전체가 #REF! 로 깨진다. 그래서 행마다 한 칸씩 쓴다.
# 조회가 비면 빈칸이 되므로, 회사 행이 없을 수 있는 경우에는 부르지 않는다.
_REGISTRY_TAB = "고객 기본 정보"
_REGISTRY_COLUMNS = {"company": 3, "company_type": 5, "country": 6}


def update_registry_company(client_id: int | None, company: str | None) -> bool:
    """고객 기본 정보의 **고객사 이름**을 고칩니다. 워크북에서 그 이름이 사는 유일한 곳입니다.

    Inbound DB 의 고객사 칸은 이 탭을 Client ID 로 조회하는 수식이라(`_write_registry_formulas`),
    여기 한 번 고치면 그 문의 행도 따라 바뀝니다 — 반대로 Inbound DB 쪽을 값으로 덮으면 그
    행만 수식이 끊깁니다.

    이 탭에서 우리가 쓰는 칸은 C(고객사)뿐입니다. 기업 종류·국가는 문의가 처음 들어올 때
    한 번 채우고 그 뒤로는 운영자 것이며, Website URL 과 최초 연락일은 애초에 시트가
    원본입니다. 그래서 **한 칸만** 씁니다.
    """
    if not client_id or not (company or "").strip() or not writes_enabled():
        return False
    try:
        service = _build_service()
        spreadsheet_id = settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip()
        ids = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{_REGISTRY_TAB}'!A2:A")
            .execute()
            .get("values")
            or []
        )
        target = str(client_id).replace(",", "").strip()
        row = next(
            (
                index + 2  # A2 부터 읽었으므로 첫 행이 2행입니다.
                for index, value in enumerate(ids)
                if value and str(value[0]).replace(",", "").strip() == target
            ),
            None,
        )
        if row is None:
            logger.info("고객 기본 정보에 Client ID %s 행이 없어 이름을 못 고쳤습니다.", client_id)
            return False
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{_REGISTRY_TAB}'!C{row}",
            valueInputOption="RAW",  # 글자입니다 — USER_ENTERED 로 보내면 수식으로 읽힐 수 있습니다.
            body={"values": [[company.strip()]]},
        ).execute()
        logger.info("고객 기본 정보 %s행의 고객사를 고쳤습니다 (client_id=%s).", row, client_id)
        return True
    except Exception as exc:
        logger.warning(
            "고객 기본 정보 이름 수정 실패 (client_id=%s): %s: %s",
            client_id, type(exc).__name__, exc, exc_info=True,
        )
        return False


def _ensure_registry_row(service, record: dict) -> None:
    """그 Client ID 의 회사 행이 고객 기본 정보에 없으면 만든다.

    조회는 대상이 있어야 값을 준다. 새 회사의 첫 문의는 회사 행도 그때 처음 생기므로,
    문의 행을 쓰기 **전에** 여기서 만든다. 이미 있으면 손대지 않는다 — 운영자가 고쳐 둔
    이름을 문의 한 번에 되돌리면 안 된다.
    """

    client_id = record.get("client_id")
    if not client_id:
        return
    spreadsheet_id = settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip()
    known = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{_REGISTRY_TAB}'!A2:A")
        .execute()
        .get("values")
        or []
    )
    target = str(client_id).replace(",", "").strip()
    if any(row and str(row[0]).replace(",", "").strip() == target for row in known):
        return
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{_REGISTRY_TAB}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={
            "values": [
                [
                    client_id,
                    "",  # 고객 종류는 수식이다
                    record.get("company") or "",
                    "",  # Website URL 은 시트가 원본이라 비워 둔다
                    record.get("company_type") or "",
                    record.get("country") or "",
                    # 담당부서도 수식이다 — 바로 위 고객 종류(B)와 같다. 값으로 쓰면
                    # 「배열 결과가 데이터를 덮어쓰게 되어」 G열 전체가 #REF! 가 되고,
                    # 그 뒤로 그 열은 아무 행도 계산하지 않는다. 이 칸이 값이었던 것은
                    # 순서 때문이다: 이 append 를 쓸 때 G 는 아직 손으로 적는 칸이었고,
                    # 나중에 워크북을 지으면서 수식 열이 됐다(build_won_sheets.py 의 array.G).
                    "",
                    record.get("inquiry_date") or "",
                ]
            ]
        },
    ).execute()
    logger.info("고객 기본 정보에 회사 행을 만들었습니다 (client_id=%s).", client_id)


def _write_registry_formulas(service, tab: str, header: _SheetHeader, row: int) -> None:
    """방금 쓴 행의 고객사·기업 종류·국가를 고객 기본 정보 조회로 바꾼다."""
    lookup = _header_lookup(dict.fromkeys(_REGISTRY_COLUMNS, ""))
    data = []
    for index, cell in enumerate(header.values):
        key = _key_for_header(cell, lookup)
        column = _REGISTRY_COLUMNS.get(key or "")
        if column is None:
            continue
        data.append(
            {
                "range": f"'{tab}'!{_column_letter(index)}{row}",
                "values": [
                    [
                        f"=IFERROR(VLOOKUP($A{row},'{_REGISTRY_TAB}'!$A:$J,{column},FALSE),\"\")"
                    ]
                ],
            }
        )
    if data:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()


def _write_pipeline_formula(service, tab: str, header: _SheetHeader, row: int) -> None:
    """Put the formula in Pipeline for a row that was just written.

    Separate from the append because the row number is only known from its response, and
    the formula has to name that row. USER_ENTERED, or Sheets stores the text of it.
    """
    lookup = _header_lookup({"pipeline": ""})
    pipeline_index = next(
        (i for i, cell in enumerate(header.values) if _key_for_header(cell, lookup) == "pipeline"),
        None,
    )
    formula = _pipeline_formula(header, row)
    if pipeline_index is None or formula is None:
        logger.info("Sheet has no Pipeline/Plan column pair; skipping the formula.")
        return
    service.spreadsheets().values().update(
        spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
        range=f"'{tab}'!{_column_letter(pipeline_index)}{row}",
        valueInputOption="USER_ENTERED",
        body={"values": [[formula]]},
    ).execute()


def _row_number(response: dict) -> int | None:
    updated_range = str((response.get("updates") or {}).get("updatedRange") or "")
    digits = "".join(ch for ch in updated_range.rsplit("!", 1)[-1].split(":", 1)[0] if ch.isdigit())
    return int(digits) if digits else None


def _existing_row_for_keys(
    service,
    tab: str,
    header: _SheetHeader,
    record: dict,
    keys: tuple[str, ...],
) -> int | None:
    """Return an existing row matching all stable business keys."""
    lookup = _header_lookup({key: "" for key in keys})
    indexes: dict[str, int] = {}
    for index, cell in enumerate(header.values):
        key = _key_for_header(cell, lookup)
        if key:
            indexes[key] = index
    if set(indexes) != set(keys):
        raise GoogleSheetsError(f"Sheet is missing idempotency columns: {', '.join(keys)}")
    rows = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
            range=f"'{tab}'!A{header.first_data_row}:{header.last_column}",
        )
        .execute()
        .get("values")
        or []
    )
    expected = {key: str(record.get(key, "") or "").replace(",", "").strip() for key in keys}
    for offset, row in enumerate(rows, start=header.first_data_row):
        if all(
            str(row[indexes[key]] if indexes[key] < len(row) else "").replace(",", "").strip()
            == expected[key]
            for key in keys
        ):
            return offset
    return None


def _update_existing_nonempty_cells(
    service, tab: str, header: _SheetHeader, row: int, record: dict
) -> None:
    """Update app-owned non-empty values without clearing operator-maintained cells."""
    lookup = _header_lookup(record)
    data = []
    for index, cell in enumerate(header.values):
        key = _key_for_header(cell, lookup)
        value = record.get(key) if key else None
        if key and value not in (None, ""):
            data.append(
                {
                    "range": f"'{tab}'!{_column_letter(index)}{row}",
                    "values": [[value]],
                }
            )
    if data:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
            body={"valueInputOption": "RAW", "data": data},
        ).execute()


def _append_existing_tab(
    record: dict,
    tab: str,
    *,
    allocate_client_id: bool = False,
    dedup_keys: tuple[str, ...] = (),
    registry: bool = False,
) -> SheetWriteResult:
    if not writes_enabled():
        raise GoogleSheetsError("Google Sheets credentials are not configured.")
    # Keep max-id allocation + append indivisible inside a process. Production
    # uses one sync worker; the lock also protects local webhook/poller overlap.
    with _APPEND_LOCK:
        service = _build_service()
        header = _headers(service, tab)
        payload = dict(record)
        legacy_inquiry_keys = tuple(payload.pop("_legacy_inquiry_keys", ()) or ())
        if payload.get("inquiry_key"):
            header = _ensure_inquiry_key_column(service, tab, header)
        client_id = payload.get("client_id")
        if allocate_client_id and not client_id:
            client_id = _next_inbound_client_id(service, tab, header)
            payload["client_id"] = client_id
        if registry:
            _ensure_registry_row(service, payload)
        for legacy_key in legacy_inquiry_keys:
            if client_id and legacy_key:
                _backfill_legacy_inquiry_key(
                    service, tab, header, int(client_id), str(legacy_key)
                )
        if dedup_keys:
            existing_row = _existing_row_for_keys(service, tab, header, payload, dedup_keys)
            if existing_row:
                _update_existing_nonempty_cells(service, tab, header, existing_row, payload)
                _write_pipeline_formula(service, tab, header, existing_row)
                if registry:
                    _write_registry_formulas(service, tab, header, existing_row)
                return SheetWriteResult(
                    row=existing_row,
                    client_id=int(client_id) if client_id else None,
                )
        row = _mapped_row(header.values, payload)
        response = (
            service.spreadsheets()
            .values()
            .append(
                # Anchor the table at the header, not A1: with a label row above it,
                # A1 makes Sheets treat that row as the table and append beside it.
                spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
                range=f"'{tab}'!A{header.row}",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            )
            .execute()
        )
        appended = _row_number(response)
        if appended:
            _write_pipeline_formula(service, tab, header, appended)
            if registry:
                _write_registry_formulas(service, tab, header, appended)
    return SheetWriteResult(row=appended, client_id=int(client_id) if client_id else None)


def append_inbound_row(record: dict) -> SheetWriteResult:
    return _append_existing_tab(
        record,
        settings.GOOGLE_SHEETS_INBOUND_TAB.strip() or "Inbound DB",
        allocate_client_id=True,
        # New rows are inquiries; Client ID is deliberately shared by a company.
        # Keep the legacy fallback for callers/old workbooks without Inquiry ID.
        dedup_keys=("inquiry_key",) if record.get("inquiry_key") else ("client_id",),
        registry=True,
    )


def suggest_inbound_client_id() -> int:
    """Return the next visible 1000-series ID for local reservation.

    Callers reserve this value on the conversation before any external append,
    giving retries a durable idempotency key if a Sheets response is lost.
    """
    if not writes_enabled():
        raise GoogleSheetsError("Google Sheets credentials are not configured.")
    with _APPEND_LOCK:
        service = _build_service()
        tab = settings.GOOGLE_SHEETS_INBOUND_TAB.strip() or "Inbound DB"
        return _next_inbound_client_id(service, tab, _headers(service, tab))


def read_inbound_records(limit: int = 5000) -> list[dict]:
    """Read existing Inbound DB rows without changing the workbook."""
    if not is_configured():
        raise GoogleSheetsError("Google Sheets credentials are not configured.")
    service = _build_service()
    tab = settings.GOOGLE_SHEETS_INBOUND_TAB.strip() or "Inbound DB"
    header = _headers(service, tab)
    client_id_lookup = _header_lookup({"client_id": ""})
    if not any(
        _key_for_header(cell, client_id_lookup) == "client_id" for cell in header.values
    ):
        raise GoogleSheetsError("Inbound DB has no Client ID column; refusing to import it.")
    first = header.first_data_row
    rows = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
            range=f"'{tab}'!A{first}:{header.last_column}{max(first, limit + first - 1)}",
        )
        .execute()
        .get("values")
        or []
    )
    records = []
    for row_number, row in enumerate(rows[:limit], start=first):
        record = _record_from_row(header.values, row)
        if any(str(value or "").strip() for value in record.values()):
            record["_row"] = row_number
            records.append(record)
    return records


def append_order_row(record: dict) -> SheetWriteResult:
    return _append_existing_tab(
        record,
        settings.GOOGLE_SHEETS_ORDERS_TAB.strip() or "수주 DB",
        dedup_keys=("client_id", "order_date"),
    )


# Local stage -> the workbook's (Deal Stage, Deal Stage Detail) pair. Only the values
# the sales team already uses in those columns may appear here — writing a new token
# would pollute a column the team filters on. "won" inherits ("Won", "Closed Won") from
# the retired contracted/onboarding/active keys (migration 0040).
#
# **HubSpot 이 단계 이름을 바꿔도 여기 글자는 안 바뀝니다.** 파이프라인에서 Meeting link
# sent 가 Qualified 가 되었지만, 이 열은 영업팀이 필터로 쓰는 시트의 값 목록이라 없는 말을
# 쓰면 그 행이 어느 필터에도 안 걸립니다. 시트에 Qualified 가 생기면 그때 여기를 바꿉니다.
#
# `closed` 는 예외입니다 — 운영자가 시트에도 적으라고 정했습니다 (2026-08-19). 그 전까지는
# 적을 말이 없어 **그 단계로 옮겨도 시트가 옛 값 그대로 남았습니다**(경고만 남기고). 이제
# 허브스팟에서 No Response 와 Not a Fit 이 Concluded 하나로 합쳐졌으므로, 시트에도 그 한
# 마디를 적습니다. Detail 은 새 말을 만들지 않고 이미 쓰는 "Closed Lost" 를 씁니다: 못 딴
# 채로 끝난 건이라는 뜻의 칸이 시트에 이미 있고, 거기에 또 다른 말을 들이면 같은 뜻의 값이
# 둘이 됩니다.
#
# **시트의 Deal Stage 목록에 Concluded 가 없으면 먼저 추가해야 합니다** — 드롭다운에 없는
# 값을 쓰면 그 행이 어느 필터에도 안 걸립니다. 그게 이 표를 다섯 개로 묶어 두었던 이유입니다.
#
# `reminder_sent` 는 여전히 말이 없습니다. 시트에 그 칸을 쓸 말이 정해지면 여기 한 줄입니다.
_STAGE_VALUES = {
    "new": ("New", "Inquiry"),
    "meeting_link_sent": ("Meeting Link Sent", "Inquiry"),
    "negotiation": ("Negotiation", "Meeting"),
    "won": ("Won", "Closed Won"),
    "closed_lost": ("Lost_Rejected", "Closed Lost"),
    "closed": ("Concluded", "Closed Lost"),
}


def _rows_for_value(
    service, tab: str, header: _SheetHeader, key: str, value: object
) -> list[int]:
    lookup = _header_lookup({key: ""})
    index = next(
        (idx for idx, cell in enumerate(header.values) if _key_for_header(cell, lookup) == key),
        None,
    )
    if index is None:
        return []
    column = _column_letter(index)
    first = header.first_data_row
    values = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
            range=f"'{tab}'!{column}{first}:{column}",
        )
        .execute()
        .get("values")
        or []
    )
    target = str(value).replace(",", "").strip()
    return [
        offset + first
        for offset, row in enumerate(values)
        if row and str(row[0]).replace(",", "").strip() == target
    ]


def inbound_client_id_rows(client_id: int) -> list[int]:
    """Read the Inbound DB row numbers carrying an exact Client ID."""
    if not is_configured():
        return []
    service = _build_service()
    tab = settings.GOOGLE_SHEETS_INBOUND_TAB.strip() or "Inbound DB"
    header = _headers(service, tab)
    return _rows_for_value(service, tab, header, "client_id", client_id)


def replace_inbound_client_id(source_id: int, target_id: int) -> int:
    """Replace a duplicate Client ID without touching each inquiry's unique row key."""
    if not is_configured():
        return 0
    from ..common.safe_mode import guard_external_write

    guard_external_write("sheets:merge_client_id")
    service = _build_service()
    tab = settings.GOOGLE_SHEETS_INBOUND_TAB.strip() or "Inbound DB"
    header = _headers(service, tab)
    rows = _rows_for_value(service, tab, header, "client_id", source_id)
    lookup = _header_lookup({"client_id": ""})
    index = next(
        idx
        for idx, cell in enumerate(header.values)
        if _key_for_header(cell, lookup) == "client_id"
    )
    column = _column_letter(index)
    if rows:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
            body={
                "valueInputOption": "RAW",
                "data": [
                    {"range": f"'{tab}'!{column}{row}", "values": [[target_id]]}
                    for row in rows
                ],
            },
        ).execute()
    logger.info(
        "Replaced Inbound DB Client ID %s with %s on %d row(s).",
        source_id,
        target_id,
        len(rows),
    )
    return len(rows)


def _row_for_inquiry(
    service,
    tab: str,
    header: _SheetHeader,
    client_id: int,
    inquiry_key: str | None = None,
) -> int | None:
    """Find one inquiry row without guessing between shared Client IDs."""
    if inquiry_key:
        exact = _rows_for_value(service, tab, header, "inquiry_key", inquiry_key)
        if exact:
            return exact[0]
    rows = _rows_for_value(service, tab, header, "client_id", client_id)
    if len(rows) > 1:
        raise GoogleSheetsError(
            f"Inbound DB Client ID {client_id} has {len(rows)} inquiry rows; "
            "an Inquiry ID is required."
        )
    return rows[0] if rows else None


def _sheet_id(service, tab: str) -> int | None:
    """탭 이름 -> gid. 행 삭제는 A1 표기가 아니라 이 번호로만 됩니다."""
    meta = (
        service.spreadsheets()
        .get(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
            fields="sheets(properties(sheetId,title))",
        )
        .execute()
    )
    for sheet in meta.get("sheets") or ():
        properties = sheet.get("properties") or {}
        if str(properties.get("title") or "").strip() == tab:
            return properties.get("sheetId")
    return None


def delete_inbound_row(client_id: int | None, inquiry_key: str | None = None) -> bool:
    """그 Client ID 의 Inbound DB 행을 **지웁니다**. 콘솔에서 사라진 문의는 시트에서도 사라집니다.

    부르는 곳은 `hubspot_reconcile.delete_conversation` 한 곳입니다 — 티켓이 허브스팟에서
    지워졌거나 우리 파이프라인 밖으로 옮겨져 그 문의가 우리 것이 아니게 된 두 경우. 안 지우면
    같은 문의를 콘솔은 없다고 하고 시트는 있다고 해서, 두 화면의 건수가 영영 안 맞습니다.

    **줄을 비우지 않고 지웁니다.** 비우면 가운데에 빈 줄이 쌓이고(append 는 늘 맨 아래로
    갑니다), 문의 월·분기처럼 배열 수식이 채우는 칸은 애초에 값을 지울 수 없습니다. 줄을
    지우면 배열이 알아서 다시 계산합니다. 저장된 행 번호(`Conversation.sheet_inbound_row`)가
    아래로 밀리는 것은 괜찮습니다 — 그 값은 「이미 붙였다」는 표시로만 쓰이고, 실제 쓰기는
    매번 Client ID 로 행을 다시 찾습니다.

    행이 없으면 조용히 False 입니다. 이관 전 문의이거나 append 가 아직 안 된 문의라 지울
    것이 없는 것이지, 실패가 아닙니다.
    """
    if not client_id or not writes_enabled():
        return False
    try:
        service = _build_service()
        tab = settings.GOOGLE_SHEETS_INBOUND_TAB.strip() or "Inbound DB"
        header = _headers(service, tab)
        row = _row_for_inquiry(service, tab, header, client_id, inquiry_key)
        if row is None:
            logger.info("Inbound DB 에 Client ID %s 행이 없어 지울 것이 없습니다.", client_id)
            return False
        sheet_id = _sheet_id(service, tab)
        if sheet_id is None:
            raise GoogleSheetsError(f"워크북에서 '{tab}' 탭을 못 찾았습니다.")
        service.spreadsheets().batchUpdate(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
            body={
                "requests": [
                    {
                        "deleteDimension": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "ROWS",
                                # 0-기반 반열림 구간입니다. row 는 1-기반 행 번호라 그대로
                                # 넣으면 **다음 행**이 지워집니다.
                                "startIndex": row - 1,
                                "endIndex": row,
                            }
                        }
                    }
                ]
            },
        ).execute()
        logger.info("Inbound DB %s행(Client ID %s)을 지웠습니다.", row, client_id)
        return True
    except Exception as exc:
        logger.warning(
            "Inbound DB 행 삭제 실패 (client_id=%s): %s: %s",
            client_id, type(exc).__name__, exc, exc_info=True,
        )
        return False


def update_inbound_stage(
    client_id: int | None,
    stage: str,
    inquiry_key: str | None = None,
) -> bool:
    """Find the stable Client ID and update only its current stage cells.

    **Pipeline(MQL/PQL) 칸에는 값을 쓰지 않습니다 — 오히려 수식을 되돌려 놓습니다**
    (2026-09-02 운영자 지시).

    예전에는 `pipeline` 인자를 받아 `customer_profiles.qualification` 을 그대로 적었습니다.
    그 열은 **워크북 전체 동기화가 시트의 수식 결과를 베껴 온 사본**이라, 여기서 되돌려
    쓰는 순간 그 행의 수식이 죽은 글자로 바뀌었습니다. 그 뒤로 그 행은 구독 플랜을 아무리
    고쳐도 옛 값을 그대로 들고 있고, 옆 행들과 달리 수식이 없다는 것은 시트를 봐도 안
    보입니다. 인자를 「안 넘기면」이 아니라 **없애는** 이유는, 남겨 두면 다음 호출자가
    언젠가 또 그리로 가기 때문입니다.

    그래서 단계를 옮기는 김에 그 칸의 수식을 다시 깝니다 — 이미 덮여 버린 행이 다음 단계
    이동에서 스스로 낫습니다. 그러지 않으면 「이제 값을 안 쓴다」로 고쳐 놓고도 시트는
    그대로라, 화면에서는 고쳐진 것이 없어 보입니다.
    """
    if not client_id or not writes_enabled():
        return False
    if stage not in _STAGE_VALUES:
        # 보드는 일곱 단계이고 워크북의 Deal Stage 열에는 그중 여섯의 말이 있습니다
        # (`reminder_sent` 만 없습니다). 조용히 지나가면 시트가 옛 단계를 그대로 들고
        # 있는데 왜 그런지는 아무 데도 안 남습니다 — 로그에라도 적습니다.
        logger.warning(
            "워크북의 단계 열%s 에 '%s' 단계를 적을 말이 없어 그 행은 이전 값을 유지합니다 "
            "(client_id=%s). google_sheets._STAGE_VALUES 에 한 줄을 더하면 됩니다.",
            _ALIASES["deal_stage"],
            stage,
            client_id,
        )
        return False
    try:
        service = _build_service()
        tab = settings.GOOGLE_SHEETS_INBOUND_TAB.strip() or "Inbound DB"
        header = _headers(service, tab)
        if inquiry_key:
            header = _ensure_inquiry_key_column(service, tab, header)
        row = _row_for_inquiry(service, tab, header, client_id, inquiry_key)
        if row is None:
            raise GoogleSheetsError(f"Inbound DB Client ID {client_id} was not found.")
        words = _STAGE_VALUES.get(stage)
        if words is None:
            # 시트의 Deal Stage 열은 영업팀이 필터로 쓰는 **값 목록**이라, 없는 말을 지어
            # 넣으면 그 행이 어느 필터에도 안 걸립니다. 그래서 안 쓰고, 대신 어떤 단계가
            # 빠져 있는지 남깁니다 — 목록에 넣을 말은 영업팀이 정할 일입니다.
            logger.warning(
                "워크북에 '%s' 단계를 적을 말이 없어 Client ID %s 행을 건너뜁니다. "
                "_STAGE_VALUES 에 그 단계의 시트 표기를 추가해야 합니다.",
                stage, client_id,
            )
            return False
        values = {"deal_stage": words[0], "deal_stage_detail": words[1]}
        if inquiry_key:
            values["inquiry_key"] = inquiry_key
        lookup = _header_lookup(values)
        data = []
        for index, cell in enumerate(header.values):
            key = _key_for_header(cell, lookup)
            if key:
                column = _column_letter(index)
                data.append({"range": f"'{tab}'!{column}{row}", "values": [[values[key]]]})
        if len(data) < 2:
            # **찾은 헤더를 그대로 적습니다.** 예전에는 「stage columns were not found」
            # 한 줄이었는데, 그러면 시트에 실제로 무슨 열이 있는지 알 길이 없어 사람이
            # 시트를 열어 눈으로 맞춰 보는 수밖에 없었습니다. 이 워크북은 2026년에 영문
            # 이름으로 다시 만들어져 그 두 칸이 Ticket Status / Deal Detail 이 되었고,
            # 코드의 키 이름(deal_stage)만 옛 이름으로 남아 로그가 시트에 없는 이름을
            # 말하고 있었습니다.
            raise GoogleSheetsError(
                "Inbound DB 에서 단계 열을 못 찾았습니다. 찾는 이름: "
                f"{_ALIASES['deal_stage']} / {_ALIASES['deal_stage_detail']}. "
                f"시트의 헤더: {[str(v) for v in header.values if str(v).strip()]}"
            )
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
        # 위 batch 에 못 싣는 이유는 `valueInputOption` 입니다: 단계 값들은 RAW 로 가야
        # 하고(`+82 10-…` 같은 글자가 수식이 되면 안 됩니다) 수식은 USER_ENTERED 로 가야
        # 합니다 — 한 요청에 하나뿐이라 호출이 둘입니다.
        _write_pipeline_formula(service, tab, header, row)
        return True
    except Exception as exc:
        # **이유를 메시지에 넣습니다.** `/logs` 는 메시지 한 줄만 보관하므로 exc_info 는
        # 화면에 안 남습니다 — 「실패했습니다」만 보이고 왜인지는 서버 로그를 따로 봐야
        # 했습니다. 그 한 줄이 열 이름 문제인지 권한 문제인지를 가릅니다.
        logger.warning(
            "Google Sheets stage update failed (client_id=%s, stage=%s): %s: %s",
            client_id,
            stage,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return False


# 이 함수로 쓸 수 있는 열. **수식 칸은 여기 없습니다** — 값으로 덮으면 시트가 스스로
# 계산하던 것이 그 행에서만 멈추고, 화면 어디에도 그게 안 보입니다. 특히 두 칸이 그렇습니다:
#
#   기업 종류(산업군) = 「고객 기본 정보」를 Client ID 로 조회하는 수식
#   Pipeline(MQL/PQL) = IF(OR(구독 플랜="", ="N/A", ="Free" …),"MQL","PQL")
#
# 뒤엣것은 **구독 플랜을 쓰면 저절로 따라옵니다.** 그래서 여기서 할 일이 없습니다.
# (2026-08-26 운영자 지시: 수식 칸은 안 건드린다.)
SYNCABLE_INBOUND_FIELDS = frozenset({"plan", "user_seq", "space_seq"})


def update_inbound_fields(client_id: int | None, values: dict[str, str]) -> int:
    """그 Client ID 의 **모든 행**에 이름 붙은 칸 몇 개를 씁니다. 고친 행 수를 돌려줍니다.

    한 행이 아니라 모든 행인 이유: 플랜도 user seq 도 **그 고객의 값**이지 그 문의의 값이
    아닙니다. 같은 회사가 세 번 문의했으면 세 행이고, 한 행만 고치면 나머지 둘이 옛 플랜을
    들고 앉아 영업팀 필터에 다르게 걸립니다.

    빈 문자열은 「지워라」입니다 — 콘솔의 빈 칸이 허브스팟에서 뜻하는 것과 같습니다. None 은
    「안 넘겼다」라서 건너뜁니다.

    실패해도 raise 하지 않습니다: 시트가 안 되는 것이 허브스팟 저장을 되돌릴 이유는 아니고,
    이유는 로그에 남습니다.
    """
    if not client_id or not writes_enabled():
        return 0
    unknown = set(values) - SYNCABLE_INBOUND_FIELDS
    if unknown:
        # 조용히 거르지 않습니다. 부르는 쪽이 쓴다고 믿은 값이 안 써지면, 그 사실이
        # 어디엔가는 적혀 있어야 다음 사람이 찾습니다.
        logger.warning("워크북에 쓸 수 없는 칸이라 건너뜁니다: %s", sorted(unknown))
    writable = {k: v for k, v in values.items() if k in SYNCABLE_INBOUND_FIELDS and v is not None}
    if not writable:
        return 0
    try:
        service = _build_service()
        tab = settings.GOOGLE_SHEETS_INBOUND_TAB.strip() or "Inbound DB"
        header = _headers(service, tab)
        rows = _rows_for_value(service, tab, header, "client_id", client_id)
        if not rows:
            logger.info("Inbound DB 에 Client ID %s 행이 없어 건너뜁니다.", client_id)
            return 0
        lookup = _header_lookup(writable)
        data = []
        for row in rows:
            for index, cell in enumerate(header.values):
                key = _key_for_header(cell, lookup)
                if key:
                    column = _column_letter(index)
                    data.append(
                        {"range": f"'{tab}'!{column}{row}", "values": [[writable[key]]]}
                    )
        if not data:
            logger.warning(
                "Inbound DB 에서 %s 열을 못 찾았습니다. 시트의 헤더: %s",
                sorted(writable),
                [str(v) for v in header.values if str(v).strip()],
            )
            return 0
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
            # 글자입니다 — 숫자로 읽히면 user seq 의 앞자리 0 이 사라집니다.
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
        return len(rows)
    except Exception as exc:
        logger.warning(
            "Google Sheets 필드 갱신 실패 (client_id=%s, fields=%s): %s: %s",
            client_id, sorted(writable), type(exc).__name__, exc, exc_info=True,
        )
        return 0


def record_inbound(record: dict) -> SheetWriteResult | None:
    if not writes_enabled():
        return None
    try:
        return append_inbound_row(record)
    except Exception:
        logger.warning("Google Sheets inbound append failed (continuing).", exc_info=True)
        return None


def record_order(record: dict) -> SheetWriteResult | None:
    if not writes_enabled():
        return None
    try:
        return append_order_row(record)
    except Exception:
        logger.warning("Google Sheets order append failed (continuing).", exc_info=True)
        return None


# connection_summary() lived here to feed the /pipeline Sheets panel and nothing else.
# The panel was removed, so it went with it. The real predicates — is_configured() and
# writes_enabled() — are above and still used throughout.
