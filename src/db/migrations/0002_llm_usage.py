"""Add llm_usage table for tracking LLM API call costs.

Portable across SQLite and Postgres — relies on SQLAlchemy metadata so the
generated DDL matches the target dialect (no raw `AUTOINCREMENT` / `datetime('now')`).
"""

from __future__ import annotations

from sqlalchemy import Engine, Index

from ..base import Base
from .. import models  # noqa: F401 — register all models with Base


def up(engine: Engine) -> None:
    """Create llm_usage table and its index if not already present."""
    table = Base.metadata.tables["llm_usage"]
    table.create(engine, checkfirst=True)
    # Index on created_at — already declared on the column, but create_all
    # only handles named indexes via __table_args__. Create here defensively.
    Index("ix_llm_usage_created_at", table.c.created_at).create(engine, checkfirst=True)
