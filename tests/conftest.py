"""Shared test fixtures."""

from __future__ import annotations

import os
import tempfile

# --- Hermetic test environment -------------------------------------------------
# The suite must pass in ANY environment, including CI, where there is no .env
# file, no ./data directory, and no secrets. These vars are set BEFORE importing
# anything under src.* so the Settings singleton and the SQLAlchemy engine pick
# them up. In pydantic-settings, real env vars take precedence over .env values,
# so this ALSO guarantees local runs never touch the real (Supabase) database.
#
# `setdefault` means an explicit override (CI secret, or `DATABASE_URL=...` on
# the command line) still wins.
_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), "sales_automation_test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB_PATH}")
os.environ.setdefault("INTERNAL_API_TOKEN", "test-internal-token")
# A dummy HubSpot token so HubSpotClient() constructs instead of raising
# HubSpotNotConfigured. Tests that exercise HubSpot mock the actual HTTP methods;
# this only satisfies the constructor's token check (matching local .env, where
# the full suite passes — no test depends on the token being absent).
os.environ.setdefault("HUBSPOT_PRIVATE_APP_TOKEN", "test-hubspot-token")
# Keep the send-redirect test override OFF during tests so a developer's local
# .env (SEND_OVERRIDE_EMAIL=...) can't leak in and silently reroute/force-SMTP
# the dispatch tests. setdefault still lets CI opt in explicitly.
os.environ.setdefault("SEND_OVERRIDE_EMAIL", "")

from unittest.mock import MagicMock  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from src.db.base import Base  # noqa: E402
from src.db import models as _models  # noqa: F401,E402 — register all models with Base
from src.db.session import engine as _real_engine  # noqa: E402

# Create the schema on the module-level engine (the temp sqlite file above) so
# tests that use the real SessionLocal — scheduler, knowledge cache, pollers,
# dual dispatcher, web UI routes — have tables to query. Guarded to sqlite so we
# can never accidentally CREATE TABLE against a real Postgres/Supabase URL.
if _real_engine.url.get_backend_name() == "sqlite":
    Base.metadata.create_all(_real_engine)


@pytest.fixture()
def db_engine():
    """In-memory SQLite engine with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_session_factory(db_engine):
    """Sessionmaker bound to the in-memory engine."""
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture()
def db_session(db_session_factory) -> Session:
    """A single DB session, closed after the test."""
    session = db_session_factory()
    yield session
    session.close()


@pytest.fixture()
def mock_llm():
    """MagicMock LLMClient returning 'ok' by default."""
    llm = MagicMock()
    llm.complete.return_value = "ok"
    return llm


