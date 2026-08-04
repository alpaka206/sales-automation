"""로컬 → 서버 정책 밀어넣기.

어느 기계도 혼자서는 못 합니다: 담당자 PC 는 노션을 읽지만 DB 포트가 막혀 있고, 서버는 DB 에
쓰지만 노션 토큰이 없습니다. 열려 있는 것은 PC 에서 서버로 가는 HTTPS 뿐이라, 두 반쪽을 그
위로 잇습니다. 이 파일이 지키는 것은 그 이음매의 성질입니다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app
from src.common.config import settings

_URL = "https://app.notion.com/p/3a2f11f6ee6380ab815afed3cbb42d77"


def _token() -> dict[str, str]:
    return {"X-Internal-Token": settings.INTERNAL_API_TOKEN}


def test_a_browser_session_cannot_reach_it():
    """콘솔 로그인으로는 열리지 않습니다. 이 경로는 스크립트용이고, 내부 토큰을 가진 쪽만
    씁니다 — /api/ui 처럼 web UI 경로로 취급되면 세션만으로 열려 버립니다."""
    from src.api.security import is_web_ui_path

    assert not is_web_ui_path("/api/policy/sources")
    assert not is_web_ui_path("/api/policy/push")

    with TestClient(app) as client:
        assert client.get("/api/policy/sources").status_code == 401
        assert client.post("/api/policy/push", json={"pages": []}).status_code == 401


def test_the_server_decides_what_gets_stored_not_the_uploader():
    """올린 쪽은 본문만 건넵니다. 어떤 행이 갱신되는지는 서버의 등록부가 정합니다.

    이 경로가 등록까지 하지 않는 것은 의도입니다: 로컬 러너는 먼저 서버에 무엇이 등록돼
    있는지 묻고 그것만 읽어 옵니다. 등록은 zip 을 드롭할 때 파일이 스스로 합니다.
    """
    from src.db.models import PolicySource
    from src.db.session import SessionLocal

    unknown = "https://www.notion.so/deadbeefdeadbeefdeadbeefdeadbeef"
    before = SessionLocal().query(PolicySource).count()
    with TestClient(app) as client:
        response = client.post(
            "/api/policy/push",
            headers=_token(),
            json={"pages": [{"notion_url": unknown, "title": "몰래", "markdown": "본문"}]},
        )
    assert response.status_code == 200
    with SessionLocal() as session:
        assert session.query(PolicySource).count() == before, "이 경로는 행을 만들지 않습니다"
        assert (
            session.query(PolicySource)
            .filter(PolicySource.label == "몰래")
            .count()
            == 0
        )


def test_an_unreadable_url_is_ignored_rather_than_failing_the_batch():
    """한 페이지의 URL 이 이상하다고 나머지 갱신이 통째로 실패하면, 고칠 때까지 아무것도
    최신화되지 않습니다."""
    with TestClient(app) as client:
        response = client.post(
            "/api/policy/push",
            headers=_token(),
            json={"pages": [{"notion_url": "not-a-url", "markdown": "본문"}]},
        )
    assert response.status_code == 200


def test_storage_has_exactly_one_implementation():
    """콘솔 업로드로 넣은 것과 스크립트로 넣은 것이 다르게 저장되면, 무엇이 최신인지가
    경로마다 달라집니다. 양쪽 모두 sync_policy_sources 를 통과해야 합니다."""
    import inspect

    from src.api import policy_api
    from src.api.routes import policy_docs

    assert "sync_policy_sources" in inspect.getsource(policy_api.push_pages)
    assert "sync_policy_sources" in inspect.getsource(policy_docs.policy_docs_upload_export)


def test_the_client_verifies_tls_against_the_os_store():
    """사내망은 HTTPS 를 사설 루트로 재서명합니다. 파이썬은 certifi 만 믿어서 같은 주소가
    브라우저에서만 열립니다. 검증을 끄는 것이 아니라 브라우저가 보는 저장소를 보게 합니다 —
    verify=False 가 들어오면 이 테스트가 막습니다."""
    import inspect

    from src.integrations import policy_push

    source = inspect.getsource(policy_push)
    assert "use_os_trust_store" in source
    assert "verify=False" not in source
