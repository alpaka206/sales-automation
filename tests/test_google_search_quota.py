"""Tests for Google Custom Search config and quota management."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.integrations.google_search import (
    GoogleSearchNotConfigured,
    GoogleSearchQuotaExceeded,
    _check_quota,
    _get_quota_usage,
    _require_config,
    _save_quota_usage,
    _track_quota,
)


def test_require_config_no_api_key() -> None:
    with patch("src.integrations.google_search.settings") as s:
        s.GOOGLE_CSE_API_KEY = ""
        s.GOOGLE_CSE_ID = "cx-test"
        with pytest.raises(GoogleSearchNotConfigured, match="API_KEY"):
            _require_config()


def test_require_config_no_cse_id() -> None:
    with patch("src.integrations.google_search.settings") as s:
        s.GOOGLE_CSE_API_KEY = "key-test"
        s.GOOGLE_CSE_ID = ""
        with pytest.raises(GoogleSearchNotConfigured, match="CSE_ID"):
            _require_config()


def test_require_config_success() -> None:
    with patch("src.integrations.google_search.settings") as s:
        s.GOOGLE_CSE_API_KEY = "key"
        s.GOOGLE_CSE_ID = "cx"
        key, cx = _require_config()
        assert key == "key"
        assert cx == "cx"


def test_get_quota_usage_no_file(tmp_path) -> None:
    with patch("src.integrations.google_search.QUOTA_FILE", str(tmp_path / "missing.json")):
        usage = _get_quota_usage()
        assert usage == {"date": "", "used": 0}


def test_get_quota_usage_existing(tmp_path) -> None:
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"date": "2025-06-01", "used": 30}))
    with patch("src.integrations.google_search.QUOTA_FILE", str(f)):
        usage = _get_quota_usage()
        assert usage["used"] == 30


def test_save_quota_usage(tmp_path) -> None:
    f = tmp_path / "sub" / "usage.json"
    with patch("src.integrations.google_search.QUOTA_FILE", str(f)):
        _save_quota_usage({"date": "2025-06-01", "used": 10})
        assert json.loads(f.read_text())["used"] == 10


def test_check_quota_new_day_allows(tmp_path) -> None:
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"date": "2020-01-01", "used": 9999}))
    with patch("src.integrations.google_search.QUOTA_FILE", str(f)):
        _check_quota(1)


def test_check_quota_exceeded(tmp_path) -> None:
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"date": today, "used": 100}))
    with patch("src.integrations.google_search.QUOTA_FILE", str(f)):
        with pytest.raises(GoogleSearchQuotaExceeded):
            _check_quota(1)


def test_check_quota_within_limit(tmp_path) -> None:
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"date": today, "used": 50}))
    with patch("src.integrations.google_search.QUOTA_FILE", str(f)):
        _check_quota(1)


def test_track_quota_new_day(tmp_path) -> None:
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"date": "2020-01-01", "used": 50}))
    with patch("src.integrations.google_search.QUOTA_FILE", str(f)):
        _track_quota(1)
        data = json.loads(f.read_text())
        assert data["used"] == 1


def test_track_quota_same_day(tmp_path) -> None:
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"date": today, "used": 10}))
    with patch("src.integrations.google_search.QUOTA_FILE", str(f)):
        _track_quota(1)
        data = json.loads(f.read_text())
        assert data["used"] == 11


def test_track_quota_warning_logged(tmp_path) -> None:
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"date": today, "used": 95}))
    with patch("src.integrations.google_search.QUOTA_FILE", str(f)):
        _track_quota(1)
        data = json.loads(f.read_text())
        assert data["used"] == 96
