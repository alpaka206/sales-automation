"""
Google Sheets mirror for inbound inquiries.

Optional, feature-flagged (``GSHEETS_ENABLED``). When on, every processed
inbound inquiry is appended as a row to a Google Sheet so non-technical
operators get an at-a-glance log without opening the app/DB.

Auth uses a DEDICATED Google credential, separate from Vertex AI: a
service-account JSON in ``GOOGLE_SHEETS_CREDENTIALS_JSON``. Share the target
spreadsheet with that service account's client email (Editor) so it can append.

The SDK (`google-api-python-client`) is imported lazily so a missing package or
missing config never crashes import or the inbound pipeline — failures are
logged and swallowed by ``record_inbound``.
"""

from __future__ import annotations

import json
import logging

from ..common.config import settings

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Stable column order. Adding a column here is safe; existing rows keep their
# positions and the header is (re)written when missing.
HEADERS: list[str] = [
    "processed_at",
    "message_id",
    "status",
    "category",
    "score",
    "channel",
    "full_name",
    "email",
    "company",
    "country",
    "subject",
    "summary",
    "inbound_excerpt",
]

# Per-process guard so we only check/write the header row once per tab.
_header_ensured: set[str] = set()


class GoogleSheetsError(RuntimeError):
    """Raised when Sheets credentials/config are missing or the API call fails."""


def is_configured() -> bool:
    """True when the feature flag and required config are all present."""
    return bool(
        settings.GSHEETS_ENABLED
        and settings.GOOGLE_SHEETS_CREDENTIALS_JSON.strip()
        and settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip()
    )


def _build_service():
    """Create a Sheets API service from the dedicated service-account JSON."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as e:  # pragma: no cover
        raise GoogleSheetsError(
            "google-api-python-client not installed. "
            "Run `pip install google-api-python-client google-auth`."
        ) from e

    raw = settings.GOOGLE_SHEETS_CREDENTIALS_JSON.strip()
    if not raw:
        raise GoogleSheetsError("GOOGLE_SHEETS_CREDENTIALS_JSON is empty.")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as e:
        raise GoogleSheetsError(f"GOOGLE_SHEETS_CREDENTIALS_JSON is not valid JSON: {e}") from e

    credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _ensure_header(service, spreadsheet_id: str, tab: str) -> None:
    """Write the header row once if the first row is empty."""
    key = f"{spreadsheet_id}:{tab}"
    if key in _header_ensured:
        return
    resp = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{tab}!1:1")
        .execute()
    )
    if not resp.get("values"):
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{tab}!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()
        logger.info("Initialized Google Sheet header row on tab %s.", tab)
    _header_ensured.add(key)


def append_inbound_row(record: dict) -> None:
    """Append one inbound record to the configured sheet. Raises on failure."""
    service = _build_service()
    spreadsheet_id = settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip()
    tab = settings.GOOGLE_SHEETS_INBOUND_TAB.strip() or "Inbound"

    _ensure_header(service, spreadsheet_id, tab)

    row = [str(record.get(col, "") if record.get(col) is not None else "") for col in HEADERS]
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{tab}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def record_inbound(record: dict) -> bool:
    """
    Safe entry point for the inbound pipeline.

    Returns True if a row was appended, False if the feature is off or the
    append failed. Never raises — Sheets is a best-effort mirror and must not
    break inbound processing.
    """
    if not is_configured():
        return False
    try:
        append_inbound_row(record)
        return True
    except Exception:
        logger.warning("Google Sheets append failed (continuing).", exc_info=True)
        return False
