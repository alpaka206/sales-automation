"""Rename conversations.topic -> inquiry_subject, and drop the AI category it held.

``topic`` stored two unrelated things. The inbound path wrote the LLM's "문의 유형"
category into it (purchase_inquiry / pricing_question / spam / …); the HubSpot backfill
wrote the ticket's subject (``hubspot_backfill.py``). Only the second was ever useful to
an operator, and the operator has retired 문의 유형 from the UI entirely.

So the column keeps the useful meaning under an honest name, and the category values are
cleared rather than carried forward as fake subjects. The category itself still exists at
runtime — it routes knowledge docs and adjusts the lead score within a single inbound run
— it is simply no longer persisted or displayed.

Idempotent and dialect-safe: SQLite (3.25+) and PostgreSQL both support
``ALTER TABLE … RENAME COLUMN``. Re-running finds the new name already present and stops.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, bindparam, inspect, text

logger = logging.getLogger(__name__)

# Everything the classifier could emit (src/llm/prompts/inbound/classify.md). These are
# categories, not subjects, so they are wiped instead of being renamed into place.
_CATEGORY_VALUES = (
    "purchase_inquiry",
    "partnership",
    "pricing_question",
    "support",
    "recruiting",
    "spam",
    "other",
)


def up(engine: Engine) -> None:
    insp = inspect(engine)
    if "conversations" not in insp.get_table_names():
        logger.info("0041: conversations table absent, skipping")
        return
    columns = {c["name"] for c in insp.get_columns("conversations")}

    if "inquiry_subject" not in columns:
        if "topic" not in columns:
            logger.info("0041: neither topic nor inquiry_subject present, skipping")
            return
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE conversations RENAME COLUMN topic TO inquiry_subject")
            )
        logger.info("0041: renamed conversations.topic -> inquiry_subject")

    # Clear the values that were categories rather than subjects. Runs on every pass so a
    # crash between the rename and the wipe still converges. Backfilled rows hold real
    # ticket subjects and are left alone.
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE conversations SET inquiry_subject = NULL "
                "WHERE inquiry_subject IN :values"
            # expanding=True is required: without it SQLAlchemy binds the tuple as a
            # single parameter and the driver sees "IN ?".
            ).bindparams(bindparam("values", expanding=True)),
            {"values": list(_CATEGORY_VALUES)},
        )
        if result.rowcount:
            logger.info("0041: cleared %s stored category values", result.rowcount)
