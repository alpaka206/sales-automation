"""Tiny migration runner. Tracks applied migrations in a _migrations table."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import Column, DateTime, MetaData, String, Table, inspect, text

from .session import engine

logger = logging.getLogger(__name__)

_TRACKER = "_migrations"
# Stable signed bigint spelling "PERSOMIG". A session-level Postgres advisory
# lock serializes schema changes across overlapping deploys.
_POSTGRES_LOCK_KEY = 0x504552534F4D4947


@contextmanager
def _migration_lock(eng) -> Iterator[None]:
    """Serialize migrations on Postgres; SQLite remains single-process only."""
    if eng.dialect.name != "postgresql":
        yield
        return

    with eng.connect() as connection:
        connection.execute(
            text("SELECT pg_advisory_lock(:key)"),
            {"key": _POSTGRES_LOCK_KEY},
        )
        logger.info("Acquired Postgres migration advisory lock.")
        try:
            yield
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": _POSTGRES_LOCK_KEY},
            )
            logger.info("Released Postgres migration advisory lock.")


def _ensure_tracker(eng) -> None:
    insp = inspect(eng)
    if _TRACKER not in insp.get_table_names():
        meta = MetaData()
        Table(
            _TRACKER,
            meta,
            Column("name", String, primary_key=True),
            Column("applied_at", DateTime, nullable=False),
        )
        meta.create_all(eng)


def _applied(eng) -> set[str]:
    with eng.connect() as conn:
        rows = conn.execute(text(f"SELECT name FROM {_TRACKER}"))
        return {r[0] for r in rows}


def run_migrations() -> list[str]:
    """Apply pending migrations in order. Returns list of newly applied names."""
    with _migration_lock(engine):
        _ensure_tracker(engine)
        already = _applied(engine)

        from src.db import migrations as pkg

        mods = sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name)
        applied_now: list[str] = []

        for _finder, name, _ispkg in mods:
            if name.startswith("_"):
                continue
            if name in already:
                logger.info("Migration %s already applied, skipping.", name)
                continue

            mod = importlib.import_module(f"src.db.migrations.{name}")
            logger.info("Applying migration %s ...", name)
            mod.up(engine)

            with engine.begin() as conn:
                conn.execute(
                    text(f"INSERT INTO {_TRACKER} (name, applied_at) VALUES (:n, :t)"),
                    {"n": name, "t": datetime.now(timezone.utc)},
                )
            applied_now.append(name)
            logger.info("Migration %s applied.", name)

        return applied_now


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    applied = run_migrations()
    print(f"Applied {len(applied)} migration(s): {applied}" if applied else "No pending migrations.")
