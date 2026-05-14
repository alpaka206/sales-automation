"""Tests for n8n workflow JSON exports - validates structure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

N8N_DIR = Path(__file__).parent.parent / "n8n_workflows"

EXPECTED_WORKFLOWS = [
    "01_inbound_webhook.json",
    "02_outbound_cron.json",
    "03_reply_check.json",
    "04_approval_card.json",
    "05_daily_report.json",
    "06_weekly_report.json",
]


@pytest.mark.parametrize("filename", EXPECTED_WORKFLOWS)
def test_workflow_is_valid_json(filename: str) -> None:
    path = N8N_DIR / filename
    assert path.exists(), f"{filename} not found"

    data = json.loads(path.read_text(encoding="utf-8"))
    assert "nodes" in data, f"{filename} missing 'nodes' key"
    assert "connections" in data, f"{filename} missing 'connections' key"
    assert isinstance(data["nodes"], list)
    assert len(data["nodes"]) > 0


@pytest.mark.parametrize("filename", EXPECTED_WORKFLOWS)
def test_workflow_has_name(filename: str) -> None:
    path = N8N_DIR / filename
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "name" in data


def test_all_six_workflows_exist() -> None:
    for f in EXPECTED_WORKFLOWS:
        assert (N8N_DIR / f).exists(), f"Missing workflow: {f}"
