"""문서를 넣는 세 번째와 네 번째 길: 붙여넣기, 그리고 고치기.

zip 드롭이 주된 경로인 이유는 문서를 만든 사람과 콘솔에 등록하는 사람이 같아야 하는 구조를
피하기 위해서입니다(docs/정책문서-동기화-설계.md §3). 그건 그대로 두고, "한 문서만 고치겠다"
와 "zip 만들기 귀찮다" 를 위해 두 길을 더 열었습니다.

여기서 확인하는 것은 하나입니다: **콘솔에서 바꾼 것이 초안이 읽는 사본까지 간다.** 안 가면
화면에는 새 내용이 보이는데 회신은 옛 내용으로 나가고, 그건 눈치챌 방법이 없습니다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db.models import KnowledgeDocument, PolicySource


@pytest.fixture()
def policy_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with (
        patch("src.api.routes.policy_docs.SessionLocal", factory),
        patch("src.agents.policy_sync.SessionLocal", factory),
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
        doc = session.query(KnowledgeDocument).one()
        assert "크레딧을 복구" in doc.body
        assert doc.status == "active"


def test_the_same_name_twice_is_refused_rather_than_split_in_two(policy_db):
    """같은 문서가 두 행이 되면 라우터가 한 정책을 두 번 인용하고, 한쪽만 고친 뒤로는
    서로 다른 두 정책이 됩니다."""
    with TestClient(app) as client:
        _create(client, body="첫 번째")
        assert client.post("/policy-docs", data={"label": "CS 문의 대응 가이드"}).status_code == 400


def test_editing_the_body_reaches_the_copy_the_draft_reads(policy_db):
    """이것이 이 파일의 요점입니다. 등록부만 고치고 사본을 두면, 화면에는 새 내용이
    보이는데 회신은 옛 내용으로 나갑니다."""
    with TestClient(app) as client:
        source_id = _create(client, body="옛 내용")
        response = client.put(f"/policy-docs/{source_id}", data={"body": "새 내용"})
        assert response.status_code == 200, response.text

    with policy_db() as session:
        assert session.query(KnowledgeDocument).one().body == "새 내용"


def test_a_console_edit_is_stamped_so_the_screen_can_say_it_will_be_overwritten(policy_db):
    """노션에서 온 문서를 여기서 고치면 그 문서를 담은 zip 을 다시 올릴 때 되돌아갑니다.
    덮어쓰는 것이 문제가 아니라 조용히 덮어쓰는 것이 문제라, 시각을 남깁니다."""
    with policy_db() as session:
        session.add(
            PolicySource(
                label="B2B 리드 대응 정책",
                notion_url="https://www.notion.so/abc",
                notion_page_id="a" * 32,
                mode="knowledge",
                body="노션에서 온 내용",
            )
        )
        session.commit()
        source_id = session.query(PolicySource).one().id

    with TestClient(app) as client:
        assert client.put(f"/policy-docs/{source_id}", data={"body": "손으로 고친 내용"}).status_code == 200

    with policy_db() as session:
        assert session.get(PolicySource, source_id).edited_at is not None


def test_a_pasted_document_is_not_touched_by_the_next_upload(policy_db):
    """빈 notion_url 이 곧 "동기화 대상 아님" 입니다. 업로드가 이 문서를 건드리면
    붙여넣은 내용이 파일에 없다는 이유로 사라집니다."""
    from src.agents.policy_sync import sync_policy_sources

    with TestClient(app) as client:
        _create(client, body="여기서만 관리하는 문서")

    def fetcher(_url):  # pragma: no cover - 호출되면 그 자체가 실패입니다
        raise AssertionError("붙여넣은 문서를 읽으러 가면 안 됩니다")

    result = sync_policy_sources(fetcher=fetcher)
    assert result["skipped"] == 1
    with policy_db() as session:
        assert session.query(PolicySource).one().body == "여기서만 관리하는 문서"
