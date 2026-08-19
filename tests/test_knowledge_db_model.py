"""Tests for KnowledgeDocument model and import script."""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.db.base import Base
from src.db.models import KnowledgeDocument


@pytest.fixture()
def db():
    """Create an in-memory SQLite database with all tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_insert_and_defaults(db: Session) -> None:
    """A new KnowledgeDocument gets sensible defaults."""
    doc = KnowledgeDocument(
        title="Test Doc",
        slug="test-doc",
        categories=["pricing"],
        body="Hello world",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    assert doc.id is not None
    assert doc.scope == "both"
    assert doc.created_at is not None
    assert doc.updated_at is not None


def test_slug_unique(db: Session) -> None:
    """Two docs with the same slug must violate unique constraint."""
    db.add(KnowledgeDocument(title="A", slug="dup", categories=[], body="a"))
    db.commit()
    db.add(KnowledgeDocument(title="B", slug="dup", categories=[], body="b"))
    with pytest.raises(Exception):
        db.commit()


def test_categories_json(db: Session) -> None:
    """categories column stores a JSON list and round-trips correctly."""
    cats = ["pricing_question", "purchase_inquiry"]
    doc = KnowledgeDocument(title="T", slug="json-test", categories=cats, body="body")
    db.add(doc)
    db.commit()
    db.refresh(doc)

    assert doc.categories == cats


def test_scope_values(db: Session) -> None:
    """scope accepts inbound and shared documents."""
    for scope in ("inbound", "both"):
        doc = KnowledgeDocument(
            title=f"scope-{scope}",
            slug=f"scope-{scope}",
            categories=[],
            scope=scope,
            body="text",
        )
        db.add(doc)
    db.commit()
    assert db.query(KnowledgeDocument).count() == 2


def test_migration_creates_table() -> None:
    """The migration module creates the table on a fresh engine."""
    engine = create_engine("sqlite:///:memory:")
    mod = importlib.import_module("src.db.migrations.0004_knowledge_documents")
    up = mod.up

    up(engine)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_documents'"))
        assert rows.fetchone() is not None


def test_migration_idempotent() -> None:
    """Running the migration twice does not raise."""
    engine = create_engine("sqlite:///:memory:")
    mod = importlib.import_module("src.db.migrations.0004_knowledge_documents")
    up = mod.up

    up(engine)
    up(engine)


def test_bundled_seeds_go_quiet_but_console_copies_do_not() -> None:
    """이관 0077 — 저장소가 들고 있던 지식 문서만 재웁니다.

    그 파일들은 손으로 돌리는 스크립트 하나만 읽었고 배포 경로에는 없었습니다. 다만 과거에
    누가 한 번 돌렸다면 그 내용이 DB 에 남아 초안이 계속 읽는데, 콘솔에는 그것을 보여 주는
    화면이 없습니다 — 운영자가 못 보고 못 고치는 문서입니다.

    **콘솔이 만든 사본은 건드리면 안 됩니다.** 규칙으로 거르지 않고 slug 열한 개를 이름으로
    적어 둔 이유가 이것이고, 그 경계가 이 검사입니다. 지우지 않고 재우는 것도 일부러입니다:
    되돌리는 것은 UPDATE 한 줄이고, 무엇이 있었는지도 남습니다.
    """
    import importlib

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                KnowledgeDocument(slug="perso_faq_dubbing", title="더빙 FAQ", body="씨앗"),
                KnowledgeDocument(slug="notion-1632872bb88d", title="콘솔 문서", body="사본"),
            ]
        )
        session.commit()

    importlib.import_module("src.db.migrations.0077_the_bundled_knowledge_seeds_go_quiet").up(engine)

    with Session(engine) as session:
        seed = session.query(KnowledgeDocument).filter_by(slug="perso_faq_dubbing").one()
        copy = session.query(KnowledgeDocument).filter_by(slug="notion-1632872bb88d").one()
        assert seed.status == "archived"
        assert copy.status == "active"
