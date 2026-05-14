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
    "07_healthcheck.json",
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


def test_all_workflows_exist() -> None:
    for f in EXPECTED_WORKFLOWS:
        assert (N8N_DIR / f).exists(), f"Missing workflow: {f}"


def _load(filename: str) -> dict:
    return json.loads((N8N_DIR / filename).read_text(encoding="utf-8"))


def _http_nodes(data: dict) -> list[dict]:
    return [n for n in data["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"]


@pytest.mark.parametrize("filename", EXPECTED_WORKFLOWS)
def test_http_nodes_have_retry_config(filename: str) -> None:
    data = _load(filename)
    for node in _http_nodes(data):
        assert node.get("retryOnFail") is True, (
            f"{filename} / {node['name']}: missing retryOnFail=true"
        )
        assert node.get("maxTries", 0) >= 3, (
            f"{filename} / {node['name']}: maxTries should be >= 3"
        )


CORE_WORKFLOWS = [
    "01_inbound_webhook.json",
    "02_outbound_cron.json",
    "03_reply_check.json",
]


@pytest.mark.parametrize("filename", CORE_WORKFLOWS)
def test_core_workflows_have_error_trigger(filename: str) -> None:
    data = _load(filename)
    error_triggers = [n for n in data["nodes"] if n["type"] == "n8n-nodes-base.errorTrigger"]
    assert len(error_triggers) >= 1, f"{filename}: missing errorTrigger node"
    slack_alerts = [n for n in data["nodes"] if "Slack Error" in n.get("name", "")]
    assert len(slack_alerts) >= 1, f"{filename}: missing Slack Error Alert node"
