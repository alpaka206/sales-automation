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
    """scope accepts inbound / outbound / both."""
    for scope in ("inbound", "outbound", "both"):
        doc = KnowledgeDocument(
            title=f"scope-{scope}",
            slug=f"scope-{scope}",
            categories=[],
            scope=scope,
            body="text",
        )
        db.add(doc)
    db.commit()
    assert db.query(KnowledgeDocument).count() == 3


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


def test_import_script_upsert(tmp_path, monkeypatch) -> None:
    """import_knowledge_base.py creates rows and is idempotent."""
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    (kb_dir / "pricing.md").write_text(
        "---\ntitle: Pricing\ncategories: [pricing_question]\n---\nBody here.",
        encoding="utf-8",
    )
    (kb_dir / "README.md").write_text("Skip me", encoding="utf-8")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    monkeypatch.setattr("scripts.import_knowledge_base.KNOWLEDGE_DIR", kb_dir)
    monkeypatch.setattr("scripts.import_knowledge_base.SessionLocal", lambda: session)

    import scripts.import_knowledge_base as imp

    imp.main()
    assert session.query(KnowledgeDocument).count() == 1
    doc = session.query(KnowledgeDocument).first()
    assert doc.slug == "pricing"
    assert doc.title == "Pricing"
    assert doc.categories == ["pricing_question"]

    (kb_dir / "pricing.md").write_text(
        "---\ntitle: Pricing Updated\ncategories: [pricing_question]\n---\nNew body.",
        encoding="utf-8",
    )
    imp.main()
    assert session.query(KnowledgeDocument).count() == 1
    db_doc = session.query(KnowledgeDocument).first()
    assert db_doc.title == "Pricing Updated"
    assert db_doc.body == "New body."
