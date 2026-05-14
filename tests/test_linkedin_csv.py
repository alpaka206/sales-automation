"""Tests for LinkedIn CSV source."""

from __future__ import annotations

import pytest
from pathlib import Path

from src.agents.outbound.sources.linkedin_csv import LinkedInCSVSource
from src.agents.outbound.source_registry import get_source


@pytest.fixture()
def csv_file(tmp_path: Path) -> Path:
    p = tmp_path / "linkedin_export.csv"
    p.write_text(
        "Full Name,Email,Company,Title,Location\n"
        "Kim Director,kim@bigco.kr,BigCo,Marketing Director,Seoul\n"
        "Jane CTO,jane@startup.io,Startup Inc,CTO,Singapore\n"
        ",,,,\n",
        encoding="utf-8",
    )
    return p


def test_linkedin_csv_parse(csv_file: Path) -> None:
    source = LinkedInCSVSource()
    results = source.discover({"path": str(csv_file)})

    assert len(results) == 2
    assert results[0].name == "Kim Director"
    assert results[0].email == "kim@bigco.kr"
    assert results[0].source == "linkedin_csv"
    assert results[0].extra["title"] == "Marketing Director"
    assert results[0].country == "Seoul"

    assert results[1].name == "Jane CTO"
    assert results[1].company == "Startup Inc"


def test_linkedin_csv_missing_file() -> None:
    source = LinkedInCSVSource()
    with pytest.raises(FileNotFoundError):
        source.discover({"path": "/nonexistent/file.csv"})


def test_registry_returns_linkedin_csv() -> None:
    source = get_source("linkedin_csv")
    assert source.name == "linkedin_csv"
