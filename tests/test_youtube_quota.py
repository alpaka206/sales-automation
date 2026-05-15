"""Tests for YouTube quota management and config checks."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from src.integrations.youtube import (
    YouTubeNotConfigured,
    YouTubeQuotaExceeded,
    _check_quota,
    _get_quota_usage,
    _require_key,
    _save_quota_usage,
    _track_quota,
)


def test_require_key_not_set() -> None:
    with patch("src.integrations.youtube.settings") as s:
        s.YOUTUBE_API_KEY = ""
        with pytest.raises(YouTubeNotConfigured):
            _require_key()


def test_require_key_success() -> None:
    with patch("src.integrations.youtube.settings") as s:
        s.YOUTUBE_API_KEY = "AIza-test"
        assert _require_key() == "AIza-test"


def test_get_quota_usage_no_file(tmp_path) -> None:
    with patch("src.integrations.youtube.QUOTA_FILE", str(tmp_path / "nonexistent.json")):
        usage = _get_quota_usage()
        assert usage == {"date": "", "used": 0}


def test_get_quota_usage_existing(tmp_path) -> None:
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"date": "2025-06-01", "used": 50}))
    with patch("src.integrations.youtube.QUOTA_FILE", str(f)):
        usage = _get_quota_usage()
        assert usage["date"] == "2025-06-01"
        assert usage["used"] == 50


def test_save_quota_usage(tmp_path) -> None:
    f = tmp_path / "sub" / "usage.json"
    with patch("src.integrations.youtube.QUOTA_FILE", str(f)):
        _save_quota_usage({"date": "2025-06-01", "used": 100})
        data = json.loads(f.read_text())
        assert data["used"] == 100


def test_track_quota_new_day(tmp_path) -> None:
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"date": "2025-01-01", "used": 5000}))
    with patch("src.integrations.youtube.QUOTA_FILE", str(f)):
        _track_quota(100)
        data = json.loads(f.read_text())
        assert data["used"] == 100


def test_track_quota_same_day(tmp_path) -> None:
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"date": today, "used": 200}))
    with patch("src.integrations.youtube.QUOTA_FILE", str(f)):
        _track_quota(50)
        data = json.loads(f.read_text())
        assert data["used"] == 250


def test_check_quota_new_day_allows(tmp_path) -> None:
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"date": "2020-01-01", "used": 99999}))
    with patch("src.integrations.youtube.QUOTA_FILE", str(f)):
        _check_quota(100)


def test_check_quota_exceeded(tmp_path) -> None:
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"date": today, "used": 9999}))
    with patch("src.integrations.youtube.QUOTA_FILE", str(f)):
        with pytest.raises(YouTubeQuotaExceeded):
            _check_quota(100)


def test_check_quota_within_limit(tmp_path) -> None:
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"date": today, "used": 100}))
    with patch("src.integrations.youtube.QUOTA_FILE", str(f)):
        _check_quota(100)
