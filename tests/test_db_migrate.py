"""Tests for src.db.migrate — migration runner."""

from __future__ import annotations

import importlib
import pkgutil
import types
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect, text


@pytest.fixture()
def mem_engine():
    """Fresh in-memory SQLite engine per test."""
    return create_engine("sqlite:///:memory:")


class TestEnsureTracker:
    def test_creates_tracker_table(self, mem_engine):
        from src.db.migrate import _ensure_tracker

        _ensure_tracker(mem_engine)
        assert "_migrations" in inspect(mem_engine).get_table_names()

    def test_idempotent(self, mem_engine):
        from src.db.migrate import _ensure_tracker

        _ensure_tracker(mem_engine)
        _ensure_tracker(mem_engine)
        assert "_migrations" in inspect(mem_engine).get_table_names()


class TestApplied:
    def test_empty_when_no_rows(self, mem_engine):
        from src.db.migrate import _applied, _ensure_tracker

        _ensure_tracker(mem_engine)
        assert _applied(mem_engine) == set()

    def test_returns_applied_names(self, mem_engine):
        from src.db.migrate import _applied, _ensure_tracker

        _ensure_tracker(mem_engine)
        with mem_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO _migrations (name, applied_at) VALUES (:n, :t)"
                ),
                {"n": "0001_initial", "t": datetime.now(timezone.utc)},
            )
        assert _applied(mem_engine) == {"0001_initial"}


class TestRunMigrations:
    def _make_migration_module(self, called: list[str], name: str):
        """Return a fake migration module with an `up()` that logs its call."""
        mod = types.ModuleType(f"src.db.migrations.{name}")

        def up(engine):
            called.append(name)

        mod.up = up
        return mod

    def test_applies_new_migrations(self, mem_engine):
        from src.db.migrate import _applied, _ensure_tracker

        called: list[str] = []
        fake_a = self._make_migration_module(called, "0001_a")
        fake_b = self._make_migration_module(called, "0002_b")

        fake_pkg = types.ModuleType("src.db.migrations")
        fake_pkg.__path__ = []

        iter_modules_return = [
            pkgutil.ModuleInfo(None, "0001_a", False),
            pkgutil.ModuleInfo(None, "0002_b", False),
        ]

        with (
            patch("src.db.migrate.engine", mem_engine),
            patch("src.db.migrate.pkgutil.iter_modules", return_value=iter_modules_return),
            patch(
                "src.db.migrate.importlib.import_module",
                side_effect=lambda name: {"src.db.migrations.0001_a": fake_a, "src.db.migrations.0002_b": fake_b}[name],
            ),
        ):
            _ensure_tracker(mem_engine)

            from src.db.migrate import run_migrations

            result = run_migrations()

        assert result == ["0001_a", "0002_b"]
        assert called == ["0001_a", "0002_b"]
        assert _applied(mem_engine) == {"0001_a", "0002_b"}

    def test_skips_already_applied(self, mem_engine):
        from src.db.migrate import _ensure_tracker

        _ensure_tracker(mem_engine)
        with mem_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO _migrations (name, applied_at) VALUES (:n, :t)"
                ),
                {"n": "0001_a", "t": datetime.now(timezone.utc)},
            )

        called: list[str] = []
        fake_a = self._make_migration_module(called, "0001_a")
        fake_b = self._make_migration_module(called, "0002_b")

        fake_pkg = types.ModuleType("src.db.migrations")
        fake_pkg.__path__ = []

        iter_modules_return = [
            pkgutil.ModuleInfo(None, "0001_a", False),
            pkgutil.ModuleInfo(None, "0002_b", False),
        ]

        with (
            patch("src.db.migrate.engine", mem_engine),
            patch("src.db.migrate.pkgutil.iter_modules", return_value=iter_modules_return),
            patch(
                "src.db.migrate.importlib.import_module",
                side_effect=lambda name: {"src.db.migrations.0001_a": fake_a, "src.db.migrations.0002_b": fake_b}[name],
            ),
        ):
            from src.db.migrate import run_migrations

            result = run_migrations()

        assert result == ["0002_b"]
        assert called == ["0002_b"]

    def test_skips_underscore_prefixed(self, mem_engine):
        from src.db.migrate import _ensure_tracker

        called: list[str] = []
        fake_a = self._make_migration_module(called, "0001_a")

        fake_pkg = types.ModuleType("src.db.migrations")
        fake_pkg.__path__ = []

        iter_modules_return = [
            pkgutil.ModuleInfo(None, "__init__", False),
            pkgutil.ModuleInfo(None, "0001_a", False),
        ]

        with (
            patch("src.db.migrate.engine", mem_engine),
            patch("src.db.migrate.pkgutil.iter_modules", return_value=iter_modules_return),
            patch(
                "src.db.migrate.importlib.import_module",
                side_effect=lambda name: {"src.db.migrations.0001_a": fake_a}[name],
            ),
        ):
            _ensure_tracker(mem_engine)

            from src.db.migrate import run_migrations

            result = run_migrations()

        assert result == ["0001_a"]
        assert called == ["0001_a"]

    def test_empty_when_no_migrations(self, mem_engine):
        fake_pkg = types.ModuleType("src.db.migrations")
        fake_pkg.__path__ = []

        with (
            patch("src.db.migrate.engine", mem_engine),
            patch("src.db.migrate.pkgutil.iter_modules", return_value=[]),
        ):
            from src.db.migrate import run_migrations

            result = run_migrations()

        assert result == []


class TestLegacyProspectStatusMigration:
    def test_skips_when_outbound_table_does_not_exist(self, mem_engine):
        migration = importlib.import_module(
            "src.db.migrations.0007_prospect_status_enum"
        )

        migration.up(mem_engine)

        assert "prospects" not in inspect(mem_engine).get_table_names()

    def test_updates_existing_legacy_rows(self, mem_engine):
        migration = importlib.import_module(
            "src.db.migrations.0007_prospect_status_enum"
        )
        with mem_engine.begin() as conn:
            conn.execute(text("CREATE TABLE prospects (id INTEGER, status TEXT)"))
            conn.execute(
                text(
                    "INSERT INTO prospects (id, status) "
                    "VALUES (1, 'candidate'), (2, 'drafted')"
                )
            )

        migration.up(mem_engine)

        with mem_engine.connect() as conn:
            statuses = conn.execute(
                text("SELECT status FROM prospects ORDER BY id")
            ).scalars().all()
        assert statuses == ["collected", "analyzed"]


class TestRetireLegacyPipelineStages:
    """Migration 0040 — the board dropped from 11 stage keys to 7."""

    MODULE = "src.db.migrations.0040_retire_legacy_pipeline_stages"

    def _seed(self, engine):
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE conversations (id INTEGER, stage TEXT)"))
            conn.execute(
                text("CREATE TABLE customer_profiles (contact_id INTEGER, pipeline_stage TEXT)")
            )
            conn.execute(
                text(
                    "INSERT INTO conversations (id, stage) VALUES "
                    "(1, 'follow_up_needed'), (2, 'contracted'), (3, 'onboarding'), "
                    "(4, 'active'), (5, 'negotiation'), (6, 'won')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO customer_profiles (contact_id, pipeline_stage) VALUES "
                    "(1, 'active'), (2, 'closed_lost')"
                )
            )

    def test_skips_when_tables_do_not_exist(self, mem_engine):
        importlib.import_module(self.MODULE).up(mem_engine)

        assert "conversations" not in inspect(mem_engine).get_table_names()

    def test_remaps_both_stage_columns_and_leaves_kept_stages_alone(self, mem_engine):
        self._seed(mem_engine)

        importlib.import_module(self.MODULE).up(mem_engine)

        with mem_engine.connect() as conn:
            stages = conn.execute(
                text("SELECT stage FROM conversations ORDER BY id")
            ).scalars().all()
            profiles = conn.execute(
                text("SELECT pipeline_stage FROM customer_profiles ORDER BY contact_id")
            ).scalars().all()
        assert stages == ["negotiation", "won", "won", "won", "negotiation", "won"]
        assert profiles == ["won", "closed_lost"]

    def test_is_idempotent(self, mem_engine):
        """migrate.py commits up() and the tracker row separately, and CI runs
        init_db.py twice — a second pass must be a no-op, not a second remap."""
        self._seed(mem_engine)
        migration = importlib.import_module(self.MODULE)

        migration.up(mem_engine)
        migration.up(mem_engine)

        with mem_engine.connect() as conn:
            stages = conn.execute(
                text("SELECT stage FROM conversations ORDER BY id")
            ).scalars().all()
        assert stages == ["negotiation", "won", "won", "won", "negotiation", "won"]

    def test_every_target_survives_the_trim(self):
        """A remap that lands on a key the board no longer renders is worse than none."""
        from src.api.web.routes.customer_ops import VALID_PIPELINE_STAGES

        migration = importlib.import_module(self.MODULE)
        assert set(migration.STAGE_MAPPING.values()) <= VALID_PIPELINE_STAGES
        assert not set(migration.STAGE_MAPPING) & VALID_PIPELINE_STAGES
