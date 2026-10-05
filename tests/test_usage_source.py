"""`/api/ui/usage-source` — 사용 데이터의 열쇠를 로그인한 브라우저에만 건넨다 (2026-10-01).

서버가 하는 일은 설정 두 칸(`USAGE_DATA_REPO` · `USAGE_DATA_TOKEN`)을 돌려주는 것뿐이다 — 데이터는
브라우저가 GitHub 에서 직접 받는다(`tests/test_usage_data_stays_off_server.py`). 그래서 지킬 것도
셋뿐이다: 비었으면 둘 다 null · 언제나 `no-store` · 다른 `/api/ui` 와 같은 로그인 관문 뒤.
"""

from __future__ import annotations

import base64
import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.common.config import settings

PATH = "/api/ui/usage-source"
REPO = "owner/processed"
TOKEN = "unit-test-token-not-a-real-one"


def _source(monkeypatch, repo: str, token: str) -> None:
    monkeypatch.setattr(settings, "USAGE_DATA_REPO", repo)
    monkeypatch.setattr(settings, "USAGE_DATA_TOKEN", token)
    # 개발자 `.env` 가 google_oauth 여도 같은 출발점에서 — 관문 테스트가 필요할 때 직접 바꾼다.
    monkeypatch.setattr(settings, "AUTH_MODE", "basic")


def _remote() -> TestClient:
    # `test_api_health._remote_client` 와 같은 모양 — 로컬이 아닌 곳에서 온 브라우저.
    return TestClient(app, base_url="https://console.example.com", client=("203.0.113.10", 50000))


def _not_stored(response) -> bool:
    # Cache-Control 은 `no-store` **한 줄**이고 ETag 도 없다 — 조건부 GET 미들웨어(모든 /api/ui 응답에 건다)가
    # no-store 답은 건너뛰기 때문이다. 건너뛰지 않으면 `no-cache` 가 한 줄 더 붙고(어느 쪽이 이기는지는 받는 쪽
    # 해석에 달린다) ETag 로 304 를 주는 길이 생긴다 — 「no-store 가 들어 있나」만 보면 그 회귀가 안 보인다.
    return response.headers.get_list("cache-control") == ["no-store"] and "etag" not in response.headers


@pytest.mark.parametrize(
    ("repo", "token"),
    [("", ""), (REPO, ""), ("", TOKEN), ("  ", " \n")],
    ids=["unset", "repo-only", "token-only", "blank"],
)
def test_unset_or_half_set_hands_over_nothing(monkeypatch, repo, token):
    """반쪽 값은 안 건넨다 — 그걸로 GitHub 를 두드리면 설정 문제가 「연결 실패」로 보인다."""
    _source(monkeypatch, repo, token)
    response = TestClient(app).get(PATH)
    assert response.status_code == 200
    assert response.json() == {"repo": None, "token": None}
    assert _not_stored(response)


def test_set_hands_over_both_and_never_logs_them(monkeypatch, caplog):
    # 대시보드에 붙여 넣다 딸려 온 공백·줄바꿈은 떼어 낸다 — 그대로 두면 Authorization 헤더가 깨진다.
    _source(monkeypatch, f" {REPO} ", f"{TOKEN}\n")
    caplog.set_level(logging.DEBUG)
    response = TestClient(app).get(PATH)
    assert response.status_code == 200
    assert response.json() == {"repo": REPO, "token": TOKEN}
    assert _not_stored(response)
    assert TOKEN not in caplog.text


def test_basic_mode_hands_the_token_only_past_the_login(monkeypatch):
    _source(monkeypatch, REPO, TOKEN)
    monkeypatch.setattr(settings, "WEB_UI_USERNAME", "admin")

    monkeypatch.setattr(settings, "WEB_UI_PASSWORD", "")
    refused = _remote().get(PATH)
    assert refused.status_code == 403  # 비밀번호가 없으면 localhost 전용
    assert TOKEN not in refused.text

    monkeypatch.setattr(settings, "WEB_UI_PASSWORD", "s3cret")
    for headers in ({}, {"Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()}):
        refused = _remote().get(PATH, headers=headers)
        assert refused.status_code == 401
        assert TOKEN not in refused.text

    login = {"Authorization": "Basic " + base64.b64encode(b"admin:s3cret").decode()}
    allowed = _remote().get(PATH, headers=login)
    assert allowed.status_code == 200
    assert allowed.json()["token"] == TOKEN


def test_google_mode_hands_the_token_only_to_a_signed_in_operator(monkeypatch):
    _source(monkeypatch, REPO, TOKEN)
    monkeypatch.setattr(settings, "AUTH_MODE", "google_oauth")

    refused = _remote().get(PATH)
    assert refused.status_code == 401
    assert refused.json() == {"detail": "login required"}
    assert TOKEN not in refused.text

    # 조회 전용(viewer)도 로그인한 콘솔 사용자다 — 사용 현황 화면을 보려면 이 열쇠가 필요하다.
    viewer = {"email": "reader@estsoft.com", "name": "Reader", "role": "viewer"}
    with patch("src.api.main.current_user", return_value=viewer):
        allowed = _remote().get(PATH)
    assert allowed.status_code == 200
    assert allowed.json() == {"repo": REPO, "token": TOKEN}
    assert _not_stored(allowed)
