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
_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), f"sales_automation_test_{os.getpid()}.db")
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
# TestClient must never start real background integrations from a developer's
# local .env. Individual worker/poller tests invoke those functions explicitly.
os.environ.setdefault("INBOUND_POLL_ENABLED", "false")
os.environ.setdefault("INBOUND_WORKER_ENABLED", "false")
os.environ.setdefault("SEND_WORKER_ENABLED", "false")
# Never let tests inherit a developer's real Slack credentials/channel.  Several
# inbound-flow tests intentionally create review-ready drafts; without this
# guard those fixtures look exactly like production notifications.
os.environ.setdefault("SLACK_ENABLED", "false")
os.environ.setdefault("APPROVAL_CHANNEL", "none")
# Run tests as if live so sender/HubSpot tests exercise the real code path against
# their mocked transports (creds are neutralized below, so no real I/O happens).
# The pre-launch safe-mode guard itself is covered by tests/test_safe_mode.py,
# which flips this off per-test via monkeypatch.
os.environ.setdefault("LIVE_EXTERNAL_WRITES", "true")
# Pin the per-destination switches too, or a developer running with
# LIVE_HUBSPOT_WRITES=false in .env silently reroutes the HubSpot tests down the
# blocked path. Safe to leave permissive: the credentials below are neutralized, so
# "live" here means "exercise the real code path against a mocked transport".
os.environ.setdefault("LIVE_HUBSPOT_WRITES", "true")
os.environ.setdefault("LIVE_SHEETS_WRITES", "true")
# Hard stop for every external write path. Tests that cover a sender enable it
# explicitly and mock the transport; a developer's real .env must never receive
# an email, report, or Sheets write during `pytest`.
os.environ["INBOUND_AUTO_ACK_ENABLED"] = "false"
os.environ["SMTP_USERNAME"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["REPORT_EMAIL_TO"] = ""
# A developer's real PUBLIC_BASE_URL (e.g. the Render URL) must not leak in — it is
# the same-origin baseline for the CSRF check, so a non-empty value 403s the web
# POST tests (recovery, customer ops) whose Origin is http://testserver.
os.environ["PUBLIC_BASE_URL"] = ""
os.environ["GOOGLE_CREDENTIALS_JSON"] = ""
# Google Sheets, and this one is not theoretical: it already happened. The suite runs
# with LIVE_EXTERNAL_WRITES=true, and Sheets used to be safe here only because a grant
# lived in the DATABASE — the temp SQLite has no IntegrationCredential row, so
# is_configured() was False. Once load_grant() learned to read the refresh token from
# the environment, that accident stopped protecting anything and `pytest` began
# appending fixture rows ("Spammer", "buyer@acme.com", …) straight into the shared
# sales workbook. Blank the credential, not just the flag: assign, never setdefault,
# so no CI secret or developer .env can put it back.
os.environ["GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN"] = ""
os.environ["GOOGLE_SHEETS_ACCOUNT_EMAIL"] = ""
os.environ["GOOGLE_SHEETS_SPREADSHEET_ID"] = ""
# HubSpot stage ids, for the same reason one level down. The dummy token above turns a
# stray write into a 401 rather than a real change, but a 401 is still a request to the
# live account from someone's `pytest`. With no stage id configured, _stage_id() returns
# "" and the call is never made. Tests that need them set them explicitly (the `stages`
# fixtures in test_stage_sync.py / test_hubspot_backfill.py).
for _stage_var in (
    "HUBSPOT_TICKET_STAGE_NEW",
    "HUBSPOT_TICKET_STAGE_MEETING_LINK_SENT",
    "HUBSPOT_TICKET_STAGE_AFTER_SEND",
    "HUBSPOT_TICKET_STAGE_NEGOTIATING",
    "HUBSPOT_TICKET_STAGE_NEGOTIATION",
    "HUBSPOT_TICKET_STAGE_REMINDER_SENT",
    "HUBSPOT_TICKET_STAGE_WON",
    "HUBSPOT_TICKET_STAGE_LOST",
    "HUBSPOT_TICKET_STAGE_CLOSED_LOST",
    "HUBSPOT_TICKET_STAGE_CLOSED",
):
    os.environ[_stage_var] = ""

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


@pytest.fixture(autouse=True)
def _allow_send_in_tests(monkeypatch):
    """Put the suite on the REAL delivery path, the same reason as LIVE_EXTERNAL_WRITES.

    Two shipped email switches are lifted here so the sender / worker / dispatch tests
    exercise production behaviour instead of test-mode behaviour:

    - ``EMAIL_SENDING_ENABLED`` — if the operator ever engages the no-send switch again,
      every send test would otherwise assert a refusal.
    - ``FORCE_TEST_RECIPIENT`` — shipped True, which reroutes every message to
      ronald@estsoft.com and marks it ``test_sent``. Real delivery (``sent`` + a HubSpot
      timeline entry) is the path worth covering, and it is the one that must keep
      working the day the pin comes off.

    Safe to do: SMTP_USERNAME/PASSWORD are blanked above and every one of those tests
    substitutes a fake ``smtplib.SMTP``, so no socket is ever opened.

    Both switches' own behaviour — that they block, and that the pin outranks the master
    switch — is asserted in tests/test_safe_mode.py, which sets them back explicitly.
    """
    from src.common import safe_mode

    monkeypatch.setattr(safe_mode, "EMAIL_SENDING_ENABLED", True)
    monkeypatch.setattr(safe_mode, "FORCE_TEST_RECIPIENT", False)


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
