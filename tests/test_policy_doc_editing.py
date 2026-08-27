"""문서가 들어오는 유일한 길: 콘솔에 붙여넣기, 그리고 고치기.

노션에서 자동으로 가져오는 경로는 전부 없앴습니다 — 통합 토큰 발급 불가, 쿠키는 403,
Export zip 은 부모 페이지 한 장만 실어 옵니다(docs/정책문서-동기화-설계.md). 그래서 이
콘솔이 정책 문서의 **원본**이고, 위쪽에 아무것도 없습니다.

여기서 확인하는 것은 하나입니다: **콘솔에서 바꾼 것이 초안이 읽는 사본까지 간다.** 안 가면
화면에는 새 내용이 보이는데 회신은 옛 내용으로 나가고, 그건 눈치챌 방법이 없습니다.
"""

from __future__ import annotations

import pathlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db.models import PolicySource


@pytest.fixture()
def policy_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with (
        patch("src.api.routes.policy_docs.SessionLocal", factory),
    ):
        yield factory


def _create(client, **fields) -> int:
    response = client.post("/policy-docs", data={"label": "CS 문의 대응 가이드", **fields})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_a_pasted_document_is_readable_by_the_draft_immediately(policy_db):
    """URL 등록 폼과의 차이가 여기 있습니다. 그건 URL 만 받았고 그 URL 을 가져올 수단이
    없어서 본문이 영원히 빈 행을 만들었습니다 — 운영에서 실제로 그 상태였습니다. 본문을
    같이 받으면 만든 순간 초안이 읽습니다."""
    with TestClient(app) as client:
        _create(client, body="업로드 실패 시에는 크레딧을 복구해 드립니다.")

    with policy_db() as session:
        doc = session.query(PolicySource).one()
        assert "크레딧을 복구" in doc.body


def test_the_same_name_twice_is_refused_rather_than_split_in_two(policy_db):
    """같은 문서가 두 행이 되면 라우터가 한 정책을 두 번 인용하고, 한쪽만 고친 뒤로는
    서로 다른 두 정책이 됩니다."""
    with TestClient(app) as client:
        _create(client, body="첫 번째")
        assert client.post("/policy-docs", data={"label": "CS 문의 대응 가이드"}).status_code == 400


def test_editing_the_body_reaches_the_draft_at_once(policy_db):
    """이것이 이 파일의 요점입니다. 화면에는 새 내용이 보이는데 회신은 옛 내용으로 나가면
    눈치챌 방법이 없습니다.

    **예전에는 사본을 밀어야 했습니다** — 라우터가 ``knowledge_documents`` 를 읽었고, 그
    사본을 안 밀면 정확히 그 상태가 됐습니다. 사본이 없어졌으므로(0098) 밀 것이 없습니다:
    라우터가 이 행을 직접 읽습니다."""
    from unittest.mock import patch as _patch

    from src.llm import knowledge

    with TestClient(app) as client:
        source_id = _create(client, body="옛 내용")
        response = client.put(f"/policy-docs/{source_id}", data={"body": "새 내용"})
        assert response.status_code == 200, response.text

    with _patch.object(knowledge, "SessionLocal", policy_db):
        assert "새 내용" in knowledge.select_relevant_docs("문의", "support")


def test_the_only_date_is_the_last_save(policy_db):
    """날짜 칸이 셋이었습니다 — ``effective_on``(기준일) · ``edited_at`` · ``updated_at``.
    「언제 기준인가」를 셋이 서로 다르게 말했고, 기준일은 마이그레이션이 심은 한 행 말고는
    아무도 안 채웠습니다. 답은 마지막으로 저장한 시각 하나입니다 (0101).
    """
    from src.db.models import PolicySource as _P

    with TestClient(app) as client:
        source_id = _create(client, body="처음 내용")
        assert client.put(f"/policy-docs/{source_id}", data={"body": "고친 내용"}).status_code == 200

    with policy_db() as session:
        source = session.get(_P, source_id)
        assert source.updated_at is not None
        for gone in ("effective_on", "edited_at", "status", "order_index", "summary"):
            assert not hasattr(source, gone), gone


def test_nothing_can_overwrite_a_document_behind_the_operators_back(policy_db):
    """이 콘솔이 원본입니다. 노션에서 읽어 오는 경로가 하나라도 남아 있으면 "여기서 고쳤는데
    다음 동기화가 되돌려 놓는" 상태가 다시 생깁니다."""
    from src.agents import policy_sync

    assert not hasattr(policy_sync, "sync_policy_sources")
    assert not pathlib.Path("src/integrations/notion.py").exists()


def test_a_deleted_document_leaves_the_prompt_and_frees_its_name(policy_db):
    """운영자가 「항상 적용」 규칙 하나를 실수로 지웠고 되돌릴 방법이 없었습니다 — 그
    종류는 DB 어디에도 사본이 없어서, 저장소의 씨앗 파일에서 **원본**을 다시 넣는 것이
    최선이었고 그 사이 콘솔에서 고친 내용은 돌아오지 않았습니다.

    이제 행은 남고 ``status`` 만 바뀝니다. 읽는 쪽(``_rules_from_db``)은 이미
    ``status='active'`` 만 보므로 지운 즉시 초안에서 빠지는 것은 그대로입니다.
    """
    from src.llm.prompts import _rules_from_db

    # _rules_from_db 는 자기 안에서 src.db.session 을 import 합니다.
    with TestClient(app) as client, patch("src.db.session.SessionLocal", policy_db):
        source_id = _create(client, body="본문", mode="rules")
        assert "CS 문의 대응 가이드" in _rules_from_db()

        assert client.post(f"/policy-docs/{source_id}/delete").status_code == 200
        assert _rules_from_db() == "", "지운 규칙이 프롬프트에 남으면 지운 것이 아닙니다"
        with policy_db() as session:
            # 행이 사라집니다 — `doc_key` 가 제목에서 나오고 unique 라, 남겨 두면 그 제목을
            # 다시는 못 씁니다. 그때 내용은 판본 이력에 있습니다 (0100).
            assert session.get(PolicySource, source_id) is None

        # 되돌리기는 없습니다 — 지우면 화면에서 바로 사라지고 행만 남습니다(2026-08-27).


def test_deleting_a_reference_document_also_stops_the_router_citing_it(policy_db):
    """「문의별 참고」는 초안이 읽는 **사본**이 따로 있습니다. 등록부만 지우면 화면에서는
    사라졌는데 라우터는 계속 인용합니다 — 하드 삭제 시절이 그랬습니다."""
    with TestClient(app) as client:
        source_id = _create(client, body="환불은 영업일 5~10일", mode="knowledge")

        assert client.post(f"/policy-docs/{source_id}/delete").status_code == 200
        with policy_db() as session:
            assert session.query(PolicySource).count() == 0
        # 같은 이름으로 다시 만들 수 있습니다 — `doc_key` 가 제목에서 나오기 때문입니다.
        assert _create(client, body="다시 씁니다")


# ---- 판본 기록 ------------------------------------------------------------------


def _make_doc(client, label="정책 하나", body="첫 본문") -> int:
    response = client.post("/policy-docs", data={"label": label, "body": body, "mode": "knowledge"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_editing_a_policy_document_leaves_the_version_before_it(policy_db):
    """정책 문서에는 이력이 **없었습니다.** 그 몫이라던 ``knowledge_document_revisions`` 는
    0016 이 만들고 아무도 쓰지 않았고(0095 가 지웠습니다), 이메일 템플릿만 이력이 쌓였습니다.

    남는 것은 고치기 **직전** 본문입니다. 그래야 「저장했는데 전엔 뭐였지」에 답이 됩니다.
    """
    from src.db.models import DocumentRevision, PolicySource
    from src.db.revisions import POLICY_SOURCE, history

    with TestClient(app) as client:
        doc_id = _make_doc(client)
        client.put(f"/policy-docs/{doc_id}", data={"body": "두 번째 본문"})
        client.put(f"/policy-docs/{doc_id}", data={"body": "세 번째 본문"})

    with policy_db() as session:
        rows = history(session, POLICY_SOURCE, doc_id)
        # 만들 때는 안 남깁니다 — 이 표는 「이전 판본」이고, 갓 만든 행에는 이전이 없습니다.
        assert [r["body"] for r in rows] == ["두 번째 본문", "첫 본문"]
        assert [r["version"] for r in rows] == [2, 1]
        source = session.get(PolicySource, doc_id)
        assert source.version == 3 and source.body == "세 번째 본문"
        # 이력은 종류를 달고 삽니다 — 한 표에 이메일 템플릿과 같이 삽니다.
        assert {r.kind for r in session.query(DocumentRevision)} == {POLICY_SOURCE}


def test_the_revision_route_refuses_a_kind_it_does_not_know(policy_db):
    """``kind`` 는 경로에서 온 문자열입니다. 그대로 조회에 넣으면 아무 문자열이나 지나가고,
    그때 돌아오는 빈 목록은 「이력이 없다」와 구별되지 않습니다."""
    with TestClient(app) as client, patch("src.db.session.SessionLocal", policy_db):
        doc_id = _make_doc(client)
        assert client.get(f"/api/ui/documents/nope/{doc_id}/revisions").status_code == 400
        ok = client.get(f"/api/ui/documents/policy_source/{doc_id}/revisions")
        assert ok.status_code == 200
        assert ok.json()["kind_label"] == "정책 문서"
        assert ok.json()["revisions"] == []
