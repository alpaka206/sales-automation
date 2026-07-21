"""Tests for Settings parsing — booleans, ints, and defaults."""

from __future__ import annotations

import os
from unittest.mock import patch

from src.common.config import Settings


def test_defaults() -> None:
    # `.env` 로딩과 OS 환경 변수 영향을 차단하여 모델 기본값만 검증.
    with patch.dict(os.environ, {}, clear=True):
        s = Settings(_env_file=None)
    assert s.LLM_PROVIDER == "gemini_vertex"
    assert s.GEMINI_MODEL == "gemini-2.5-flash"
    assert s.LIVE_EXTERNAL_WRITES is False
    assert s.AUTO_SEND_THRESHOLD == 1.01
    assert s.DAILY_SEND_LIMIT == 400
    assert s.APP_PORT == 8000
    assert s.INTERNAL_API_TOKEN == ""


def test_hubspot_token_accepts_both_env_names() -> None:
    # New naming (HUBSPOT_ACCESS_TOKEN) and legacy (HUBSPOT_PRIVATE_APP_TOKEN)
    # both populate the same field.
    with patch.dict(os.environ, {"HUBSPOT_ACCESS_TOKEN": "pat-new"}, clear=True):
        assert Settings(_env_file=None).HUBSPOT_PRIVATE_APP_TOKEN == "pat-new"
    with patch.dict(os.environ, {"HUBSPOT_PRIVATE_APP_TOKEN": "pat-legacy"}, clear=True):
        assert Settings(_env_file=None).HUBSPOT_PRIVATE_APP_TOKEN == "pat-legacy"


def test_bool_from_env() -> None:
    env = {"INBOUND_POLL_ENABLED": "true", "SEND_WORKER_ENABLED": "1"}
    with patch.dict(os.environ, env, clear=False):
        s = Settings()
    assert s.INBOUND_POLL_ENABLED is True
    assert s.SEND_WORKER_ENABLED is True


def test_int_from_env() -> None:
    env = {"APP_PORT": "9999", "DAILY_SEND_LIMIT": "30"}
    with patch.dict(os.environ, env, clear=False):
        s = Settings()
    assert s.APP_PORT == 9999
    assert s.DAILY_SEND_LIMIT == 30


def test_literal_validation() -> None:
    s = Settings()
    assert s.LLM_PROVIDER == "gemini_vertex"
    assert s.APPROVAL_CHANNEL in ("slack", "none")
