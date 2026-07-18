"""Operator policy-document UI: validation, revisions, and cache invalidation."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db.models import KnowledgeDocument, KnowledgeDocumentRevision


@pytest.fixture()
def knowledge_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with patch("src.api.web.routes.knowledge.SessionLocal", factory):
        yield factory


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _payload(**changes) -> dict[str, str]:
    values = {
        "title": "환불 정책",
        "slug": "refund-policy",
        "categories": "support, pricing_question",
        "tags": "환불, 취소",
        "summary": "환불 또는 결제 취소 문의에 사용합니다.",
        "scope": "inbound",
        "status": "active",
        "body": "결제 후 14일 이내에 환불을 요청할 수 있습니다.",
    }
    values.update(changes)
    return values


def _model_data(**changes):
    values = _payload(**changes)
    values["categories"] = ["support", "pricing_question"]
    values["tags"] = ["환불", "취소"]
    return values


def test_create_document_records_initial_revision_and_resets_cache(
    client: TestClient, knowledge_db
) -> None:
    with patch("src.api.web.routes.knowledge.reset_cache") as reset:
        response = client.post("/knowledge", data=_payload(), follow_redirects=False)

    assert response.status_code == 303
    with knowledge_db() as session:
        doc = session.query(KnowledgeDocument).one()
        revision = session.query(KnowledgeDocumentRevision).one()
        assert doc.categories == ["support", "pricing_question"]
        assert doc.tags == ["환불", "취소"]
        assert revision.version == 1
        assert revision.change_note == "문서 생성"
    reset.assert_called_once_with()


def test_create_rejects_invalid_document(client: TestClient, knowledge_db) -> None:
    response = client.post("/knowledge", data=_payload(slug="잘못된 키", body=""))

    assert response.status_code == 422
    assert "영문 소문자" in response.text
    assert "정책 본문을 입력" in response.text
    with knowledge_db() as session:
        assert session.query(KnowledgeDocument).count() == 0


def test_edit_snapshots_previous_version(client: TestClient, knowledge_db) -> None:
    with knowledge_db() as session:
        doc = KnowledgeDocument(**_model_data())
        session.add(doc)
        session.commit()
        doc_id = doc.id

    update = _payload(
        title="환불·취소 정책",
        body="결제 후 7일 이내에 환불을 요청할 수 있습니다.",
        status="inactive",
    )
    update.pop("slug")
    update |= {"expected_version": "1", "change_note": "검토 중이라 비활성화"}
    with patch("src.api.web.routes.knowledge.reset_cache") as reset:
        response = client.post(f"/knowledge/{doc_id}", data=update, follow_redirects=False)

    assert response.status_code == 303
    with knowledge_db() as session:
        doc = session.get(KnowledgeDocument, doc_id)
        revision = session.query(KnowledgeDocumentRevision).one()
        assert doc is not None
        assert doc.version == 2
        assert doc.status == "inactive"
        assert revision.version == 1
        assert revision.body == "결제 후 14일 이내에 환불을 요청할 수 있습니다."
        assert revision.change_note == "검토 중이라 비활성화"
    reset.assert_called_once_with()


def test_edit_rejects_stale_version(client: TestClient, knowledge_db) -> None:
    with knowledge_db() as session:
        doc = KnowledgeDocument(**_model_data(), version=2)
        session.add(doc)
        session.commit()
        doc_id = doc.id

    update = _payload()
    update.pop("slug")
    update["expected_version"] = "1"
    response = client.post(f"/knowledge/{doc_id}", data=update)

    assert response.status_code == 409
    assert "다른 사용자가 먼저 수정" in response.text
    with knowledge_db() as session:
        assert session.get(KnowledgeDocument, doc_id).version == 2
        assert session.query(KnowledgeDocumentRevision).count() == 0


def test_list_and_history_render_korean_operator_ui(client: TestClient, knowledge_db) -> None:
    with knowledge_db() as session:
        doc = KnowledgeDocument(**_model_data())
        session.add(doc)
        session.flush()
        session.add(
            KnowledgeDocumentRevision(
                document_id=doc.id,
                slug=doc.slug,
                version=1,
                title=doc.title,
                categories=doc.categories,
                tags=doc.tags,
                summary=doc.summary,
                scope=doc.scope,
                status=doc.status,
                body=doc.body,
                change_note="문서 생성",
                edited_by="운영자",
            )
        )
        session.commit()
        doc_id = doc.id

    listing = client.get("/knowledge")
    history = client.get(f"/knowledge/{doc_id}/history")

    assert listing.status_code == 200
    assert "정책·지식 문서" in listing.text
    assert "환불 정책" in listing.text
    assert "/knowledge/new" in listing.text
    assert history.status_code == 200
    assert "변경 이력" in history.text
    assert "문서 생성" in history.text
    assert "운영자" in history.text
