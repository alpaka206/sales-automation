"""노션 Export 업로드 — 로컬에서 DB에 닿지 못하는 네트워크에서의 유일한 경로.

sync_notion_local.py 는 노션에서 읽어 **DB에 씁니다.** 사내망이 5432/6543 아웃바운드를
막고 있어 담당자 PC는 그 두 번째 절반을 못 합니다. 서버는 DB에 닿지만 노션 API 토큰이
없습니다. 양쪽을 다 할 수 있는 기계가 없다는 것이 이 기능이 존재하는 이유입니다.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


def _export_zip(pages: dict[str, str]) -> bytes:
    """노션 Markdown & CSV 내보내기와 같은 모양의 zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in pages.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def test_an_upload_needs_no_database_access_from_the_uploader():
    """업로더는 파일만 만들면 됩니다. 서버가 파싱하고 서버가 DB에 씁니다 — 담당자 PC가
    닿지 못하는 구간을 아무도 건너지 않습니다."""
    import inspect

    from src.api.routes import policy_docs

    source = inspect.getsource(policy_docs.policy_docs_upload_export)
    assert "sync_policy_sources" in source, "서버가 직접 저장해야 합니다"
    assert "fetcher_from_export" in source, "업로드한 zip 이 곧 출처입니다"


def test_a_file_that_is_not_an_export_is_refused_with_a_readable_reason():
    """확장자만 zip 인 파일, 다른 앱의 zip, 잘못 고른 파일 — 500 이 아니라 무엇이
    잘못됐는지 읽히는 400 이어야 합니다."""
    with TestClient(app) as client:
        response = client.post(
            "/policy-docs/upload-export",
            files={"export": ("notes.zip", b"this is not a zip at all", "application/zip")},
        )
    assert response.status_code == 400
    assert "내보내기" in response.json()["detail"]


def test_an_empty_file_is_refused():
    with TestClient(app) as client:
        response = client.post(
            "/policy-docs/upload-export",
            files={"export": ("empty.zip", b"", "application/zip")},
        )
    assert response.status_code == 400


def test_the_upload_is_not_kept_on_disk():
    """정책 사본은 이미 DB에 있습니다. 업로드 파일을 서버에 남기면 지워야 할 사본이
    하나 더 생길 뿐입니다."""
    import inspect

    from src.api.routes import policy_docs

    source = inspect.getsource(policy_docs.policy_docs_upload_export)
    assert "open(" not in source and "write" not in source and "tempfile" not in source


def test_a_real_export_reaches_the_parser():
    """모양이 맞는 zip 은 400 으로 튕기지 않고 동기화까지 갑니다 (등록된 문서가 없으면
    갱신 0건으로 끝납니다)."""
    payload = _export_zip({"정책 3a2f11f6ee6380ab815afed3cbb42d77.md": "# 정책\n본문"})
    with TestClient(app) as client:
        response = client.post(
            "/policy-docs/upload-export",
            files={"export": ("Export.zip", payload, "application/zip")},
        )
    assert response.status_code == 200, response.text
    assert set(response.json()) >= {"synced", "failed", "skipped"}


@pytest.mark.parametrize("size", [26 * 1024 * 1024])
def test_an_oversized_upload_is_refused_before_it_is_parsed(size):
    """워크스페이스 전체 내보내기는 큽니다. 메모리에서 읽으므로 상한이 필요합니다."""
    with TestClient(app) as client:
        response = client.post(
            "/policy-docs/upload-export",
            files={"export": ("huge.zip", b"0" * size, "application/zip")},
        )
    assert response.status_code == 413
