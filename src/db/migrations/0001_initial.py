"""Initial migration — creates all tables from models.py."""

from __future__ import annotations

from sqlalchemy import Engine

from ..base import Base
from .. import models  # noqa: F401 — import to register models with Base


def up(engine: Engine) -> None:
    """Create all tables that don't exist yet."""
    Base.metadata.create_all(engine)
