"""Standardize prospects.status values to the new enum set."""

from __future__ import annotations

from sqlalchemy import Engine, text

STATUS_MAPPING = {
    "candidate": "collected",
    "drafted": "analyzed",
}


def up(engine: Engine) -> None:
    """Migrate legacy prospect status values to standardized enum values."""
    with engine.begin() as conn:
        for old_val, new_val in STATUS_MAPPING.items():
            conn.execute(
                text("UPDATE prospects SET status = :new WHERE status = :old"),
                {"new": new_val, "old": old_val},
            )
