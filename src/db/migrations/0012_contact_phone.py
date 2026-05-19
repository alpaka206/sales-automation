"""Add phone column to contacts table for WhatsApp piggyback delivery."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    """Add contacts.phone if missing."""
    insp = inspect(engine)
    if "contacts" not in insp.get_table_names():
        logger.warning("contacts table missing; skipping phone column add.")
        return

    cols = {c["name"] for c in insp.get_columns("contacts")}
    if "phone" in cols:
        logger.info("contacts.phone already exists, skipping.")
        return

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE contacts ADD COLUMN phone VARCHAR"))
        logger.info("Added contacts.phone column.")
