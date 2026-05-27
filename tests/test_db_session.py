"""Tests for src/db/session — URL normalization and SQLite PRAGMAs."""

from __future__ import annotations

from sqlalchemy import create_engine, event, text

from src.db.session import _normalize_url, _sqlite_pragmas


def test_normalize_url_postgres_prefix():
    """postgres:// is rewritten to postgresql:// for SQLAlchemy 2.x."""
    assert _normalize_url("postgres://user:pw@host/db") == "postgresql://user:pw@host/db"


def test_normalize_url_postgresql_unchanged():
    """postgresql:// is passed through unchanged."""
    assert _normalize_url("postgresql://user:pw@host/db") == "postgresql://user:pw@host/db"


def test_normalize_url_sqlite_unchanged():
    """sqlite URLs pass through unchanged."""
    assert _normalize_url("sqlite:///data/app.db") == "sqlite:///data/app.db"


def test_sqlite_pragmas_applied():
    """_sqlite_pragmas sets the correct PRAGMAs on a SQLite connection."""
    engine = create_engine("sqlite:///:memory:")
    event.listens_for(engine, "connect")(_sqlite_pragmas)

    with engine.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
        assert fk == 1
        sync = conn.execute(text("PRAGMA synchronous")).scalar()
        assert sync == 1  # NORMAL = 1
        busy = conn.execute(text("PRAGMA busy_timeout")).scalar()
        assert busy == 5000
