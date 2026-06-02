"""Tests for the optional Google Sheets inbound mirror."""

from __future__ import annotations

import pytest

from src.integrations import google_sheets as gs


@pytest.fixture(autouse=True)
def _clear_header_guard():
    gs._header_ensured.clear()
    yield
    gs._header_ensured.clear()


def _configure(monkeypatch, enabled=True):
    monkeypatch.setattr(gs.settings, "GSHEETS_ENABLED", enabled)
    monkeypatch.setattr(gs.settings, "GOOGLE_SHEETS_CREDENTIALS_JSON", '{"x": 1}')
    monkeypatch.setattr(gs.settings, "GOOGLE_SHEETS_SPREADSHEET_ID", "SHEET123")
    monkeypatch.setattr(gs.settings, "GOOGLE_SHEETS_INBOUND_TAB", "Inbound")


class _Exec:
    def __init__(self, ret):
        self._ret = ret

    def execute(self):
        return self._ret


class _FakeValues:
    def __init__(self, store):
        self.store = store

    def get(self, spreadsheetId, range):
        return _Exec({"values": self.store.get("existing_header")})

    def update(self, spreadsheetId, range, valueInputOption, body):
        self.store["updated"] = body["values"]
        return _Exec({})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self.store.setdefault("appended", []).append(body["values"][0])
        return _Exec({})


class _FakeService:
    def __init__(self, store):
        self._v = _FakeValues(store)

    def spreadsheets(self):
        return self

    def values(self):
        return self._v


def test_is_configured_false_when_disabled(monkeypatch):
    _configure(monkeypatch, enabled=False)
    assert gs.is_configured() is False


def test_is_configured_true_when_set(monkeypatch):
    _configure(monkeypatch)
    assert gs.is_configured() is True


def test_record_inbound_noop_when_disabled(monkeypatch):
    _configure(monkeypatch, enabled=False)
    called = {"n": 0}
    monkeypatch.setattr(gs, "append_inbound_row", lambda rec: called.__setitem__("n", 1))
    assert gs.record_inbound({"email": "a@b.com"}) is False
    assert called["n"] == 0


def test_record_inbound_swallows_errors(monkeypatch):
    _configure(monkeypatch)

    def _boom(rec):
        raise RuntimeError("api down")

    monkeypatch.setattr(gs, "append_inbound_row", _boom)
    assert gs.record_inbound({"email": "a@b.com"}) is False


def test_append_writes_header_then_row_in_column_order(monkeypatch):
    _configure(monkeypatch)
    store: dict = {"existing_header": None}  # empty sheet → header gets written
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))

    gs.append_inbound_row(
        {
            "processed_at": "2026-06-02T10:00:00",
            "message_id": 42,
            "category": "pricing_question",
            "score": 88,
            "email": "buyer@corp.com",
        }
    )

    assert store["updated"] == [gs.HEADERS]
    row = store["appended"][0]
    assert len(row) == len(gs.HEADERS)
    # values land in the right columns; missing keys become ""
    assert row[gs.HEADERS.index("message_id")] == "42"
    assert row[gs.HEADERS.index("email")] == "buyer@corp.com"
    assert row[gs.HEADERS.index("company")] == ""


def test_append_skips_header_when_present(monkeypatch):
    _configure(monkeypatch)
    store: dict = {"existing_header": [gs.HEADERS]}  # header already there
    monkeypatch.setattr(gs, "_build_service", lambda: _FakeService(store))

    gs.append_inbound_row({"message_id": 1})
    assert "updated" not in store
    assert len(store["appended"]) == 1
