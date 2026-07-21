"""Tests for schema-preserving Google Sheets synchronization."""

from __future__ import annotations

import pytest

from src.integrations import google_sheets as gs


def _configure(monkeypatch):
    from src.integrations import google_oauth

    # Sheets sync is user-OAuth-only; simulate a connected Google account.
    monkeypatch.setattr(
        google_oauth,
        "load_grant",
        lambda: ({"access_token": "x", "refresh_token": "y", "scopes": []}, "sales@example.com"),
    )
    monkeypatch.setattr(gs.settings, "GOOGLE_SHEETS_SPREADSHEET_ID", "SHEET123")
    monkeypatch.setattr(gs.settings, "GOOGLE_SHEETS_INBOUND_TAB", "Inbound DB")
    monkeypatch.setattr(gs.settings, "GOOGLE_SHEETS_ORDERS_TAB", "수주 DB")


class _Exec:
    def __init__(self, ret):
        self._ret = ret

    def execute(self):
        return self._ret


class _FakeValues:
    def __init__(self, store):
        self.store = store

    def get(self, spreadsheetId, range):
        self.store.setdefault("read_ranges", []).append(range)
        if range.endswith("1:1"):
            return _Exec({"values": self.store.get("existing_header")})
        if range in self.store.get("range_values", {}):
            return _Exec({"values": self.store["range_values"][range]})
        return _Exec({"values": self.store.get("client_values", [])})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self.store["append_range"] = range
        self.store["value_input"] = valueInputOption
        self.store.setdefault("appended", []).append(body["values"][0])
        return _Exec({"updates": {"updatedRange": "'Inbound DB'!A42:Y42"}})

    def batchUpdate(self, spreadsheetId, body):
        self.store["batch"] = body
        return _Exec({})


class _FakeService:
    def __init__(self, store):
        self._v = _FakeValues(store)

    def spreadsheets(self):
        return self

    def values(self):
        return self._v


def test_user_oauth_grant_enables_sync(monkeypatch):
    _configure(monkeypatch)
    assert gs.is_configured() is True
    assert gs.writes_enabled() is True


def test_record_inbound_noop_without_connection(monkeypatch):
    from src.integrations import google_oauth

    _configure(monkeypatch)
    monkeypatch.setattr(google_oauth, "load_grant", lambda: None)  # not connected
    assert gs.record_inbound({"email": "a@b.com"}) is None


def test_record_inbound_swallows_errors(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(gs, "append_inbound_row", lambda rec: (_ for _ in ()).throw(RuntimeError()))
    assert gs.record_inbound({"email": "a@b.com"}) is None


def test_append_matches_real_inbound_headers_and_allocates_1000_id(monkeypatch):
    _configure(monkeypatch)
    store = {
        "existing_header": [["Cluent ID", "영업방향", "문의 날짜", "Deal Stage", "Contact Email"]],
        "client_values": [["1335"], ["9005"], ["not-a-number"]],
    }
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))

    result = gs.append_inbound_row(
        {
            "sales_direction": "Inbound",
            "inquiry_date": "2026-07-18",
            "deal_stage": "New",
            "email": "buyer@corp.com",
        }
    )

    assert result == gs.SheetWriteResult(row=42, client_id=1336)
    assert store["append_range"] == "'Inbound DB'!A1"
    assert store["value_input"] == "RAW"
    assert store["appended"] == [[1336, "Inbound", "2026-07-18", "New", "buyer@corp.com"]]


def test_append_refuses_empty_header_row(monkeypatch):
    _configure(monkeypatch)
    store = {"existing_header": None}
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))
    with pytest.raises(gs.GoogleSheetsError, match="no header row"):
        gs.append_inbound_row({"email": "a@b.com"})
    assert "appended" not in store


def test_inbound_retry_upserts_existing_client_id_without_duplicate(monkeypatch):
    _configure(monkeypatch)
    store = {
        "existing_header": [["Cluent ID", "문의 날짜", "Deal Stage", "Contact Email"]],
        "client_values": [["1336", "2026-07-18", "New", "old@example.com"]],
    }
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))

    result = gs.append_inbound_row(
        {
            "client_id": 1336,
            "inquiry_date": "2026-07-18",
            "deal_stage": "Negotiation",
            "email": "buyer@example.com",
        }
    )

    assert result == gs.SheetWriteResult(row=2, client_id=1336)
    assert "appended" not in store
    assert store["batch"]["valueInputOption"] == "RAW"


def test_allocated_inbound_id_is_rechecked_before_append(monkeypatch):
    _configure(monkeypatch)
    store = {
        "existing_header": [["Cluent ID", "문의 날짜", "Deal Stage", "Contact Email"]],
        "range_values": {
            "'Inbound DB'!A2:A": [["1335"]],
            # Simulate another process appending 1336 after max-ID allocation.
            "'Inbound DB'!A2:D": [["1336", "2026-07-18", "New", "other@example.com"]],
        },
    }
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))

    result = gs.append_inbound_row(
        {
            "inquiry_date": "2026-07-18",
            "deal_stage": "New",
            "email": "buyer@example.com",
        }
    )

    assert result == gs.SheetWriteResult(row=2, client_id=1336)
    assert "appended" not in store
    assert store["batch"]["valueInputOption"] == "RAW"


def test_order_header_with_id_rule_is_supported(monkeypatch):
    _configure(monkeypatch)
    store = {
        "existing_header": [[
            "* ID 규칙: GTM Inbound_1000번대 | Client ID",
            "담당부서",
            "고객분류",
            "수주일",
            "고객사",
        ]]
    }
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))
    result = gs.append_order_row(
        {
            "client_id": 1336,
            "department": "GTM",
            "customer_classification": "Inbound",
            "order_date": "2026-07-18",
            "company": "Example",
        }
    )
    assert result.row == 42
    assert store["appended"] == [[1336, "GTM", "Inbound", "2026-07-18", "Example"]]


def test_order_retry_updates_existing_business_key_without_duplicate(monkeypatch):
    _configure(monkeypatch)
    store = {
        "existing_header": [[
            "* ID 규칙: GTM Inbound_1000번대 | Client ID",
            "담당부서",
            "고객분류",
            "수주일",
            "고객사",
        ]],
        "client_values": [["1336", "GTM", "Inbound", "2026-07-18", "Old name"]],
    }
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))

    result = gs.append_order_row(
        {
            "client_id": 1336,
            "department": "GTM",
            "customer_classification": "Inbound",
            "order_date": "2026-07-18",
            "company": "Corrected name",
        }
    )

    assert result.row == 2
    assert "appended" not in store
    assert store["batch"]["valueInputOption"] == "RAW"
    assert {item["range"] for item in store["batch"]["data"]} == {
        "'수주 DB'!A2", "'수주 DB'!B2", "'수주 DB'!C2", "'수주 DB'!D2", "'수주 DB'!E2"
    }


def test_stage_update_touches_only_stage_cells(monkeypatch):
    _configure(monkeypatch)
    store = {
        "existing_header": [["Cluent ID", "Deal Stage", "Deal Stage Detail", "Pipeline", "고객사"]],
        "client_values": [[""]] * 40 + [["42"]],
    }
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))

    assert gs.update_inbound_stage(42, "negotiation", "PQL") is True
    assert store["batch"]["data"] == [
        {"range": "'Inbound DB'!B42", "values": [["Negotiation"]]},
        {"range": "'Inbound DB'!C42", "values": [["Meeting"]]},
        {"range": "'Inbound DB'!D42", "values": [["PQL"]]},
    ]
    assert store["batch"]["valueInputOption"] == "RAW"


def test_read_inbound_records_maps_real_headers_without_writing(monkeypatch):
    _configure(monkeypatch)
    store = {
        "existing_header": [[
            "Cluent ID",
            "문의 날짜",
            "Deal Stage",
            "고객사",
            "고객사 담당자",
            "Contact Email",
            "문의 히스토리",
        ]],
        "client_values": [[
            "1336",
            "2026. 07. 18.",
            "Negotiation",
            "Example Co",
            "Kim",
            "buyer@example.com",
            "Needs an enterprise quote",
        ]],
    }
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))

    records = gs.read_inbound_records(limit=10)

    assert records == [{
        "client_id": "1336",
        "inquiry_date": "2026. 07. 18.",
        "deal_stage": "Negotiation",
        "company": "Example Co",
        "full_name": "Kim",
        "email": "buyer@example.com",
        "history": "Needs an enterprise quote",
        "_row": 2,
    }]
    assert "appended" not in store


def test_read_inbound_records_fails_loudly_when_client_id_header_drifted(monkeypatch):
    _configure(monkeypatch)
    store = {
        "existing_header": [["문의 번호", "문의 날짜", "Contact Email"]],
        "client_values": [["1336", "2026-07-18", "buyer@example.com"]],
    }
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))

    with pytest.raises(gs.GoogleSheetsError, match="no Client ID column"):
        gs.read_inbound_records()
    assert "appended" not in store
    assert "batch" not in store
