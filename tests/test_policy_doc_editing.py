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


def test_an_edit_is_stamped_because_it_is_the_only_date_this_document_has(policy_db):
    """위에서 받아 오는 것이 없으므로 "마지막 동기화" 라는 값이 존재하지 않습니다. 화면이
    보여줄 수 있는 유일한 날짜가 마지막으로 손댄 시각입니다."""
    with TestClient(app) as client:
        source_id = _create(client, body="처음 내용")
        assert client.put(f"/policy-docs/{source_id}", data={"body": "고친 내용"}).status_code == 200

    with policy_db() as session:
        assert session.get(PolicySource, source_id).edited_at is not None


def test_nothing_can_overwrite_a_document_behind_the_operators_back(policy_db):
    """이 콘솔이 원본입니다. 노션에서 읽어 오는 경로가 하나라도 남아 있으면 "여기서 고쳤는데
    다음 동기화가 되돌려 놓는" 상태가 다시 생깁니다."""
    from src.agents import policy_sync

    assert not hasattr(policy_sync, "sync_policy_sources")
    assert not pathlib.Path("src/integrations/notion.py").exists()


def test_a_document_can_say_when_it_is_effective_rather_than_when_it_was_pasted(policy_db):
    """「크레딧 차감 정책 (26.04.28 기준)」을 오늘 붙여넣으면 저장 시각은 오늘입니다. 목록이
    그것만 보여주면 넉 달 된 정책이 어제 손댄 최신 문서처럼 보이고, 그 차이가 "이 숫자 아직
    맞나?" 를 물어볼지 말지를 가릅니다."""
    with TestClient(app) as client:
        source_id = _create(client, body="초 단위 차감", effective_on="2026-04-28")

    with policy_db() as session:
        assert session.get(PolicySource, source_id).effective_on == "2026-04-28"


def test_clearing_the_effective_date_hands_the_column_back_to_the_save_time(policy_db):
    """빈 값은 "안 적었다" 가 아니라 "지운다" 로 읽습니다 — 잘못 적은 날짜를 되돌릴 방법이
    없으면, 틀린 기준일이 영원히 남습니다."""
    with TestClient(app) as client:
        source_id = _create(client, body="본문", effective_on="2026-04-28")
        assert client.put(f"/policy-docs/{source_id}", data={"body": "본문2"}).status_code == 200

    with policy_db() as session:
        source = session.get(PolicySource, source_id)
        assert source.effective_on is None
        assert source.edited_at is not None
