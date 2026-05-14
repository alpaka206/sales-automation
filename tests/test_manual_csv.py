"""Tests for manual_csv source and source registry."""

from __future__ import annotations

import pytest
from pathlib import Path

from src.agents.outbound.sources.manual_csv import ManualCSVSource
from src.agents.outbound.source_registry import get_source


@pytest.fixture()
def csv_file(tmp_path: Path) -> Path:
    p = tmp_path / "prospects.csv"
    p.write_text(
        "name,email,company,domain,country,notes\n"
        "Kim Test,kim@acme.kr,Acme,acme.kr,korea,VIP lead\n"
        "Jane Doe,jane@startup.io,Startup Inc,startup.io,sg,Warm intro\n"
        "No Email,,Unknown,,us,\n",
        encoding="utf-8",
    )
    return p


def test_manual_csv_parse(csv_file: Path) -> None:
    source = ManualCSVSource()
    results = source.discover({"path": str(csv_file)})

    assert len(results) == 3
    assert results[0].name == "Kim Test"
    assert results[0].email == "kim@acme.kr"
    assert results[0].source == "manual_csv"
    assert results[0].extra["notes"] == "VIP lead"

    assert results[2].email is None
    assert results[2].name == "No Email"


def test_manual_csv_missing_file() -> None:
    source = ManualCSVSource()
    with pytest.raises(FileNotFoundError):
        source.discover({"path": "/nonexistent/file.csv"})


def test_registry_returns_manual_csv() -> None:
    source = get_source("manual_csv")
    assert source.name == "manual_csv"


def test_registry_unknown_raises() -> None:
    with pytest.raises(KeyError, match="Unknown source"):
        get_source("nonexistent")


def test_filter_domains_allow(csv_file: Path) -> None:
    source = ManualCSVSource()
    results = source.discover({
        "path": str(csv_file),
        "domains_allow": ["acme.kr"],
    })
    assert len(results) == 1
    assert results[0].name == "Kim Test"


def test_filter_domains_block(csv_file: Path) -> None:
    source = ManualCSVSource()
    results = source.discover({
        "path": str(csv_file),
        "domains_block": ["startup.io"],
    })
    names = [r.name for r in results]
    assert "Jane Doe" not in names
    assert "Kim Test" in names


def test_filter_countries(csv_file: Path) -> None:
    source = ManualCSVSource()
    results = source.discover({
        "path": str(csv_file),
        "countries": ["korea"],
    })
    assert len(results) == 1
    assert results[0].name == "Kim Test"
