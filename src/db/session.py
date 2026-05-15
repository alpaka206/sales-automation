"""Engine and SessionLocal factory."""

from __future__ import annotations

from sqlalchemy import create_engine
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
    # Postgres on Supabase / Render benefits from these defaults:
    pool_pre_ping=not _is_sqlite,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
