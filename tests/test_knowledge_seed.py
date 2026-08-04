"""The bundled Markdown seeds the database once; after that the database owns it.

This used to upsert on every run, so an operator's edit — or a Notion sync — was undone
by the next deploy that ran the importer. Keeping content in two places is only safe when
exactly one of them can write.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import scripts.import_knowledge_base as seed
from src.db.base import Base
from src.db.models import KnowledgeDocument


@pytest.fixture()
def seed_db(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    (tmp_path / "perso_pricing.md").write_text(
        "---\ntitle: 가격 정책\n---\n\n분당 $2 입니다.\n", encoding="utf-8"
    )
    monkeypatch.setattr(seed, "KNOWLEDGE_DIR", tmp_path)
    with patch.object(seed, "SessionLocal", factory):
        yield factory


def test_the_first_run_loads_the_bundled_documents(seed_db):
    seed.main()
    with seed_db() as session:
        doc = session.query(KnowledgeDocument).one()
        assert doc.slug == "perso_pricing"
        assert "분당 $2" in doc.body


def test_a_second_run_leaves_edits_alone(seed_db):
    seed.main()
    with seed_db() as session:
        doc = session.query(KnowledgeDocument).one()
        doc.body = "분당 $1.7 로 재협의됨"
        session.commit()

    seed.main()
    with seed_db() as session:
        assert session.query(KnowledgeDocument).one().body == "분당 $1.7 로 재협의됨"


def test_force_restores_the_file_copy(seed_db):
    seed.main()
    with seed_db() as session:
        session.query(KnowledgeDocument).one().body = "지워짐"
        session.commit()

    seed.main(force=True)
    with seed_db() as session:
        assert "분당 $2" in session.query(KnowledgeDocument).one().body
