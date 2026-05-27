"""Engine and SessionLocal factory."""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from ..common.config import settings


def _normalize_url(url: str) -> str:
    """
    Normalize DATABASE_URL for SQLAlchemy 2.x.

    Render/Supabase/Heroku hand out `postgres://...` URLs but SQLAlchemy 2.x
    rejects them — it expects the explicit `postgresql://` scheme.
    """
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


_url = _normalize_url(settings.DATABASE_URL)
_is_sqlite = _url.startswith("sqlite")

engine = create_engine(
    _url,
    future=True,
    echo=False,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=not _is_sqlite,
)


def _sqlite_pragmas(dbapi_conn, _connection_record):
    """Set performance and safety PRAGMAs for SQLite connections."""
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
    finally:
        cur.close()


if _is_sqlite:
    event.listens_for(engine, "connect")(_sqlite_pragmas)


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
