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
# forever. The operator's rule, 2026-08-04:
#
#     N/A                    -> MQL      (nothing bought yet; Free is written as N/A)
#     엔터프라이즈             -> 재계약    (already a customer; this is a renewal)
#     pro / creator / starter / 사용중(플랜 미확인) -> PQL
#
# No blank branch: this app always fills the plan cell, so an empty one cannot arise from
# the inbound path. A cell someone clears by hand therefore reads PQL.
#
# The plan column is located by header rather than hardcoded as N, so inserting a column
# does not silently point the formula at the wrong one.
def _pipeline_formula(header: _SheetHeader, row: int) -> str | None:
    lookup = _header_lookup({"plan": ""})
    plan_index = next(
        (i for i, cell in enumerate(header.values) if _key_for_header(cell, lookup) == "plan"),
        None,
    )
    if plan_index is None:
        return None
    cell = f"{_column_letter(plan_index)}{row}"
    return f'=IF({cell}="N/A","MQL",IF({cell}="엔터프라이즈","재계약","PQL"))'


# 고객사·기업 종류·국가는 「고객 기본 정보」가 원본이다. 문의 행마다 값을 다시 적으면 같은
# 회사가 세 번 문의했을 때 서울대학교 / 서울대 / SNU 로 갈라지고, 고쳐도 한 행만 고쳐진다.
# 그래서 이 세 칸은 Client ID 로 그 탭을 조회한다 — 거기서 한 번 고치면 전부 따라 바뀐다.
#
# ARRAYFORMULA 를 쓰지 않는 이유: 이 탭은 앱이 행을 append 하는데, 배열이 채워야 할 자리에
# 값이 들어오면 열 전체가 #REF! 로 깨진다. 그래서 행마다 한 칸씩 쓴다.
# 조회가 비면 빈칸이 되므로, 회사 행이 없을 수 있는 경우에는 부르지 않는다.
_REGISTRY_TAB = "고객 기본 정보"
_REGISTRY_COLUMNS = {"company": 3, "company_type": 5, "country": 6}


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
        client_id = payload.get("client_id")
        if allocate_client_id and not client_id:
            client_id = _next_inbound_client_id(service, tab, header)
            payload["client_id"] = client_id
        if registry:
            _ensure_registry_row(service, payload)
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
        dedup_keys=("client_id",),
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
# reminder_sent, no_response and closed(=Not a Fit) have no workbook vocabulary yet, so
# update_inbound_stage leaves the sheet untouched for them, exactly as it did before the trim.
#
# **HubSpot 이 단계 이름을 바꿔도 여기 글자는 안 바뀝니다.** 파이프라인에서 Meeting link
# sent 가 Qualified 가 되었지만, 이 열은 영업팀이 필터로 쓰는 시트의 값 목록이라 없는 말을
# 쓰면 그 행이 어느 필터에도 안 걸립니다. 시트에 Qualified 가 생기면 그때 여기를 바꿉니다.
_STAGE_VALUES = {
    "new": ("New", "Inquiry"),
    "meeting_link_sent": ("Meeting Link Sent", "Inquiry"),
    "negotiation": ("Negotiation", "Meeting"),
    "won": ("Won", "Closed Won"),
    "closed_lost": ("Lost_Rejected", "Closed Lost"),
}


def _row_for_client_id(service, tab: str, header: _SheetHeader, client_id: int) -> int | None:
    lookup = _header_lookup({"client_id": ""})
    index = next(
        (idx for idx, cell in enumerate(header.values) if _key_for_header(cell, lookup) == "client_id"),
        None,
    )
    if index is None:
        raise GoogleSheetsError("Inbound DB has no Client ID column.")
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
    target = str(client_id).replace(",", "").strip()
    return next(
        (
            offset + first
            for offset, value in enumerate(values)
            if value and str(value[0]).replace(",", "").strip() == target
        ),
        None,
    )


def update_inbound_stage(client_id: int | None, stage: str, pipeline: str | None = None) -> bool:
    """Find the stable Client ID and update only its current stage cells."""
    if not client_id or not writes_enabled():
        return False
    if stage not in _STAGE_VALUES:
        # The board has eight stages; the workbook's Deal Stage column has words for five
        # of them (reminder_sent, no_response and closed have none). Silence here meant the sheet kept
        # its old stage with nothing to show anyone why — say it in the log at least.
        logger.warning(
            "Google Sheets has no Deal Stage wording for local stage '%s' — the workbook "
            "keeps its previous value (client_id=%s).",
            stage,
            client_id,
        )
        return False
    try:
        service = _build_service()
        tab = settings.GOOGLE_SHEETS_INBOUND_TAB.strip() or "Inbound DB"
        header = _headers(service, tab)
        row = _row_for_client_id(service, tab, header, client_id)
        if row is None:
            raise GoogleSheetsError(f"Inbound DB Client ID {client_id} was not found.")
        values = {
            "deal_stage": _STAGE_VALUES[stage][0],
            "deal_stage_detail": _STAGE_VALUES[stage][1],
        }
        if pipeline:
            values["pipeline"] = pipeline
        lookup = _header_lookup(values)
        data = []
        for index, cell in enumerate(header.values):
            key = _key_for_header(cell, lookup)
            if key:
                column = _column_letter(index)
                data.append({"range": f"'{tab}'!{column}{row}", "values": [[values[key]]]})
        if len(data) < 2:
            raise GoogleSheetsError("Inbound DB stage columns were not found.")
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip(),
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
        return True
    except Exception:
        logger.warning(
            "Google Sheets stage update failed (client_id=%s, stage=%s).",
            client_id,
            stage,
            exc_info=True,
        )
        return False


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
