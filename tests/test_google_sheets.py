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
        # The header scan asks for a band of leading rows ("'Tab'!1:5") because the
        # real header is not always row 1 — see _SheetHeader.
        if range.rsplit("!", 1)[-1].startswith("1:"):
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
        # 회사 행이 이미 있으면 고객 기본 정보에는 아무것도 추가하지 않는다.
        "range_values": {"'고객 기본 정보'!A2:A": [["1336"]]},
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


def test_header_below_a_merged_group_label_row_is_found(monkeypatch):
    """The live workbook's Inbound DB puts a merged group-label row above the header.

    Row 1 there is mostly blank with "고객사" / "고객사 담당자" spanning several columns.
    Taking row 1 as the header made read_inbound_records raise "no Client ID column"
    and would have made every write land a column off.
    """
    _configure(monkeypatch)
    store = {
        # The live layout: merged group labels on row 1, real header on row 2.
        "existing_header": [
            ["", "", "", "", "고객사", "", "고객사 담당자"],
            ["Client ID", "Pipeline", "기업 종류", "Company Name",
             "Full Name", "Email", "Plan"],
            ["9001", "MQL", "기업", "한스바이오메드", "Kim", "c@hansbiomed.com", "Pro"],
        ],
        # limit=10 from the first data row (3) reads rows 3..12.
        "range_values": {
            "'Inbound DB'!A3:G12": [
                ["9001", "MQL", "기업", "한스바이오메드", "Kim", "c@hansbiomed.com", "Pro"],
            ],
        },
    }
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))

    records = gs.read_inbound_records(limit=10)

    assert len(records) == 1
    assert records[0]["client_id"] == "9001"
    # English column names from the rebuilt workbook must map like the Korean ones.
    assert records[0]["company"] == "한스바이오메드"
    assert records[0]["full_name"] == "Kim"
    assert records[0]["email"] == "c@hansbiomed.com"
    assert records[0]["plan"] == "Pro"
    # Row 3 is the first data row; the label row and the header must not be records,
    # and the number has to be the real sheet row so stage updates hit the right cell.
    assert records[0]["_row"] == 3


def test_stage_update_targets_the_row_offset_by_a_two_row_header(monkeypatch):
    """An off-by-one here overwrites a different customer's stage cells."""
    _configure(monkeypatch)
    store = {
        "existing_header": [
            ["", "", "고객사"],
            ["Client ID", "Deal Stage", "Deal Stage Detail"],
        ],
        # Column A from the first data row (3) down: 9001 is row 3, 9002 is row 4.
        "range_values": {"'Inbound DB'!A3:A": [["9001"], ["9002"]]},
    }
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))

    assert gs.update_inbound_stage(9002, "won") is True
    ranges = [item["range"] for item in store["batch"]["data"]]
    assert ranges == ["'Inbound DB'!B4", "'Inbound DB'!C4"]


def test_append_anchors_the_table_at_the_header_not_a1(monkeypatch):
    """Appending against A1 lets Sheets treat the label row as the table."""
    _configure(monkeypatch)
    store = {
        "existing_header": [
            ["", "고객사"],
            ["Cluent ID", "Deal Stage"],
        ],
    }
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))

    gs.append_inbound_row({"client_id": 1336, "deal_stage": "New"})

    assert store["append_range"] == "'Inbound DB'!A2"


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

    store.setdefault("range_values", {})["'고객 기본 정보'!A2:A"] = [["1336"]]

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
            "'고객 기본 정보'!A2:A": [["1336"]],
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


# ---- Pipeline is a formula, not a value --------------------------------------------


def test_pipeline_is_written_as_a_formula_over_the_plan_cell():
    """The sales team edits 구독 플랜 by hand as a deal moves. A written-in "MQL" would
    then sit beside a plan that contradicts it; a formula re-reads that cell forever.

    The plan column is found by header rather than hardcoded as N, so inserting a column
    cannot silently point the formula at a different one.
    """
    from src.integrations.google_sheets import _pipeline_formula, _SheetHeader

    header = _SheetHeader(
        values=["Client ID", "영업방향", "문의 날짜", "Ticket Status", "Pipeline", "구독 플랜"],
        row=1,
    )
    assert _pipeline_formula(header, 168) == (
        '=IF(F168="N/A","MQL",IF(F168="엔터프라이즈","재계약","PQL"))'
    )


def test_a_sheet_without_a_plan_column_gets_no_formula():
    """Rather than writing one that points at whatever happens to sit in N."""
    from src.integrations.google_sheets import _pipeline_formula, _SheetHeader

    assert _pipeline_formula(_SheetHeader(values=["Client ID", "Pipeline"], row=1), 5) is None


def test_the_formula_branches_match_the_operators_rule():
    """N/A -> MQL, 엔터프라이즈 -> 재계약, everything else -> PQL. No blank branch: this
    app always fills the plan cell (Free is written as N/A)."""
    from src.integrations.google_sheets import _pipeline_formula, _SheetHeader

    formula = _pipeline_formula(_SheetHeader(values=["Pipeline", "Plan"], row=1), 2)
    assert '"N/A","MQL"' in formula
    assert '"엔터프라이즈","재계약"' in formula
    assert formula.endswith('"PQL"))')


def test_a_new_company_gets_a_registry_row_before_its_inquiry_is_written(monkeypatch):
    """고객사 이름은 「고객 기본 정보」가 원본이고, Inbound DB 는 그걸 조회한다.

    조회는 대상이 있어야 값을 준다. 새 회사의 첫 문의는 회사 행도 그때 처음 생기므로,
    문의 행보다 **먼저** 회사 행을 만든다 — 순서가 뒤집히면 그 회사만 이름이 빈다.
    """
    _configure(monkeypatch)
    store = {
        "existing_header": [["Cluent ID", "문의 날짜", "Company Name", "IP Country"]],
        "client_values": [["1335"]],
    }
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))

    gs.append_inbound_row(
        {
            "inquiry_date": "2026-07-18",
            "company": "새로운 회사",
            "country": "한국",
            "company_type": "기업",
            "email": "buyer@corp.com",
        }
    )

    registry, inquiry = store["appended"]
    assert registry[:1] + registry[2:8] == [
        1336, "새로운 회사", "", "기업", "한국", "GTM", "2026-07-18"
    ]
    assert inquiry[0] == 1336
    # 문의 행의 고객사·국가는 값이 아니라 그 회사 행을 가리키는 조회다.
    formulas = [entry["values"][0][0] for entry in store["batch"]["data"]]
    assert any("'고객 기본 정보'!$A:$J" in formula for formula in formulas)
