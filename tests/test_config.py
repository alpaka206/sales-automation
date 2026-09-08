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


def test_ticket_stage_env_names_match_hubspot_labels() -> None:
    """Every stage of the [B2B] AI Dubbing pipeline must be readable from .env.

    Regression guard: the env vars were once named after internal concepts
    (AFTER_SEND / NEGOTIATION / CLOSED_LOST) rather than the HubSpot stage labels.
    Renaming them in .env to match HubSpot made pydantic's extra="ignore" drop the
    values silently, so every ticket-stage move became a no-op with no error.
    """
    env = {
        "HUBSPOT_TICKET_STAGE_NEW": "1",
        "HUBSPOT_TICKET_STAGE_MEETING_LINK_SENT": "2",
        "HUBSPOT_TICKET_STAGE_NEGOTIATING": "3",
        "HUBSPOT_TICKET_STAGE_WON": "5",
        "HUBSPOT_TICKET_STAGE_LOST": "6",
        "HUBSPOT_TICKET_STAGE_CLOSED": "7",
    }
    with patch.dict(os.environ, env, clear=True):
        s = Settings(_env_file=None)
    assert s.HUBSPOT_TICKET_STAGE_NEW == "1"
    assert s.HUBSPOT_TICKET_STAGE_AFTER_SEND == "2"
    assert s.HUBSPOT_TICKET_STAGE_NEGOTIATION == "3"
    assert s.HUBSPOT_TICKET_STAGE_WON == "5"
    assert s.HUBSPOT_TICKET_STAGE_CLOSED_LOST == "6"
    assert s.HUBSPOT_TICKET_STAGE_CLOSED == "7"


def test_ticket_stage_legacy_env_names_still_work() -> None:
    """An unmigrated .env or Render dashboard must keep resolving."""
    env = {
        "HUBSPOT_TICKET_STAGE_AFTER_SEND": "2",
        "HUBSPOT_TICKET_STAGE_NEGOTIATION": "3",
        "HUBSPOT_TICKET_STAGE_CLOSED_LOST": "6",
        "HUBSPOT_TICKET_STAGE_UNQUALIFIED": "7",
    }
    with patch.dict(os.environ, env, clear=True):
        s = Settings(_env_file=None)
    assert s.HUBSPOT_TICKET_STAGE_AFTER_SEND == "2"
    assert s.HUBSPOT_TICKET_STAGE_NEGOTIATION == "3"
    assert s.HUBSPOT_TICKET_STAGE_CLOSED_LOST == "6"
    assert s.HUBSPOT_TICKET_STAGE_CLOSED == "7"


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


def test_the_sending_address_needs_no_env_var():
    """**기본값이 곧 운영값입니다** (2026-09-08 운영자 지시: 「언제나
    perso.ai@estsoft.com 이 우선이니깐 저런 거 따로 안 해도 돼」).

    `3114216464` 가 그 주소의 채널 계정 id 입니다. 비어 있으면 「우선 주소 없음」이라
    회신이 스레드가 정하는 대로 나가는데, **그건 화면 어디에도 안 보입니다** — 실제로
    개발 머신에서 그 상태였습니다.

    그래도 **코드에 박지 않고 설정으로 둡니다**: 주소가 아니라 포털이 발급한 id 라 계정을
    다시 연결하면 바뀌고, 박아 두면 그날 발송이 조용히 다른 주소로 나가거나 실패하며
    고치려면 배포를 해야 합니다 — `.env` 한 줄이면 되는 일에.

    (`conftest` 가 이 둘을 비우므로 여기서는 필드 기본값을 직접 봅니다.)
    """
    from src.common.config import Settings

    fields = Settings.model_fields
    assert fields["HUBSPOT_PREFERRED_EMAIL_CHANNEL_ACCOUNT_ID"].default == "3114216464"
    # 고르개도 그 하나입니다 — 비우면 이 팀이 안 쓰는 `support@perso.ai` 가 섞입니다.
    assert fields["HUBSPOT_REPLY_SENDER_ACCOUNT_IDS"].default == "3114216464"
