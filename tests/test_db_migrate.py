"""Tests for src.db.migrate — migration runner."""

from __future__ import annotations

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
