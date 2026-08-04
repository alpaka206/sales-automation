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
    assert "read_export" in source, "업로드한 zip 이 곧 출처입니다"


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


def test_the_design_note_survives_and_the_code_points_at_it():
    """이 설계는 제약의 결과이지 취향이 아닙니다.

    "노션 API 쓰면 되는데" 는 합리적인 질문이고, 그 답이 코드 어디에도 없으면 누군가 —
    사람이든 모델이든 — 되돌려 놓고 왜 안 되는지를 처음부터 다시 알아내게 됩니다. 실제로
    막힌 지점(통합 토큰 발급 불가, 5432 차단, file.notion.com 403)은 전부 실행해서 확인한
    것이라 다시 알아내는 데 시간이 듭니다.

    그래서 문서가 있고, 관련 모듈이 전부 그 문서를 가리킵니다.
    """
    import pathlib

    note = pathlib.Path("docs/정책문서-동기화-설계.md")
    assert note.exists(), "설계 근거 문서가 사라졌습니다"
    text = note.read_text(encoding="utf-8")
    # 다시 검토할 때 무엇을 확인해야 하는지가 없으면 문서가 아니라 변명입니다.
    assert "Test-NetConnection" in text and "NOTION_TOKEN" in text

    pointing = [
        "src/agents/policy_sync.py",
        "src/api/policy_api.py",
        "src/integrations/notion.py",
        "src/integrations/notion_export.py",
        "src/integrations/policy_push.py",
    ]
    for path in pointing:
        assert "정책문서-동기화-설계" in pathlib.Path(path).read_text(encoding="utf-8"), path
