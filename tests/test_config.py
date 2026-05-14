"""Tests for Settings parsing — booleans, ints, and defaults."""

from __future__ import annotations

import os
from unittest.mock import patch

from src.common.config import Settings


def test_defaults() -> None:
    # `.env` 로딩과 OS 환경 변수 영향을 차단하여 모델 기본값만 검증.
    with patch.dict(os.environ, {}, clear=True):
        s = Settings(_env_file=None)
    assert s.LLM_PROVIDER == "claude_cli"
    assert s.EMAIL_PROVIDER == "hubspot"
    assert s.WHATSAPP_ENABLED is False
    assert s.AUTO_SEND_THRESHOLD == 1.01
    assert s.OUTBOUND_COOLDOWN_DAYS == 90
    assert s.APP_PORT == 8000
    assert s.INTERNAL_API_TOKEN == ""


def test_bool_from_env() -> None:
    env = {"WHATSAPP_ENABLED": "true", "LINKEDIN_ENABLED": "1"}
    with patch.dict(os.environ, env, clear=False):
        s = Settings()
    assert s.WHATSAPP_ENABLED is True
    assert s.LINKEDIN_ENABLED is True


def test_int_from_env() -> None:
    env = {"APP_PORT": "9999", "OUTBOUND_COOLDOWN_DAYS": "30"}
    with patch.dict(os.environ, env, clear=False):
        s = Settings()
    assert s.APP_PORT == 9999
    assert s.OUTBOUND_COOLDOWN_DAYS == 30


def test_literal_validation() -> None:
    s = Settings()
    assert s.LLM_PROVIDER in ("claude_cli", "anthropic_api")
    assert s.APPROVAL_CHANNEL in ("slack", "teams", "none")
