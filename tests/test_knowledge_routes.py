"""Tests for knowledge base CRUD web routes."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db import models as _models  # noqa: F401
from src.db.models import KnowledgeDocument


@pytest.fixture()
def kb_db():
    """Shared in-memory DB for knowledge CRUD tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with patch("src.api.web.routes.SessionLocal", factory), \
         patch("src.api.web.routes._reset_kb_cache", lambda: None):
        yield factory


@pytest.fixture()
def seed_doc(kb_db):
    """Insert a sample knowledge document."""
    session = kb_db()
    doc = KnowledgeDocument(
        title="요금제 안내",
        slug="pricing",
        categories=["pricing"],
        scope="both",
        body="# 요금제\n\nBasic: 무료, Pro: 10만원/월",
    )
    session.add(doc)
    session.commit()
    doc_id = doc.id
    session.close()
    return doc_id


def _client() -> TestClient:
    return TestClient(app)


def test_knowledge_list_empty(kb_db):
    r = _client().get("/knowledge")
    assert r.status_code == 200
    assert "문서가 없습니다" in r.text


def test_knowledge_list_with_docs(seed_doc):
    r = _client().get("/knowledge")
    assert r.status_code == 200
    assert "요금제 안내" in r.text


def test_knowledge_new_form(kb_db):
    r = _client().get("/knowledge/new")
    assert r.status_code == 200
    assert "새 문서 작성" in r.text


def test_knowledge_edit_form(seed_doc):
    r = _client().get(f"/knowledge/{seed_doc}")
    assert r.status_code == 200
    assert "요금제 안내" in r.text
    assert "Basic" in r.text


def test_knowledge_edit_404(kb_db):
    r = _client().get("/knowledge/9999")
    assert r.status_code == 404


def test_knowledge_create(kb_db):
    r = _client().post("/knowledge", data={
        "title": "FAQ",
        "categories": "faq,all",
        "scope": "inbound",
        "body": "자주 묻는 질문입니다.",
    })
    assert r.status_code == 200
    assert "생성 완료" in r.text
    session = kb_db()
    doc = session.query(KnowledgeDocument).filter_by(title="FAQ").first()
    assert doc is not None
    assert doc.categories == ["faq", "all"]
    assert doc.scope == "inbound"
    session.close()


def test_knowledge_create_requires_title(kb_db):
    r = _client().post("/knowledge", data={
        "title": "", "categories": "", "scope": "both", "body": "something",
    })
    assert r.status_code == 400


def test_knowledge_update(seed_doc, kb_db):
    r = _client().put(f"/knowledge/{seed_doc}", data={
        "title": "요금제 안내 v2",
        "categories": "pricing,policy",
        "scope": "both",
        "body": "수정된 요금제 안내",
    })
    assert r.status_code == 200
    assert "저장 완료" in r.text
    session = kb_db()
    doc = session.get(KnowledgeDocument, seed_doc)
    assert doc.title == "요금제 안내 v2"
    assert doc.body == "수정된 요금제 안내"
    session.close()


def test_knowledge_delete(seed_doc, kb_db):
    r = _client().delete(f"/knowledge/{seed_doc}")
    assert r.status_code == 200
    assert "삭제 완료" in r.text
    session = kb_db()
    doc = session.get(KnowledgeDocument, seed_doc)
    assert doc is None
    session.close()
