"""Keep the Korean translation next to the message: ``messages.body_ko`` / ``subject_ko``.

Opening a foreign-language ticket blocked on Gemini. Every inbound bubble that is not
already Korean was translated on each page load, and the only cache was a dict in process
memory — which Render's free plan empties every time the service sleeps. Measured on a
three-bubble English thread: six translation calls, 1.8 seconds of the operator waiting,
paid again after every spin-down.

A message body never changes, so its translation never changes. That belongs in the row,
not in a cache that forgets. First open still pays; every open after it is a column read,
across restarts and across processes.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "messages" not in set(inspector.get_table_names()):
        logger.info("0045: messages missing; skipping.")
        return
    columns = {column["name"] for column in inspector.get_columns("messages")}
    with engine.begin() as conn:
        if "body_ko" not in columns:
            conn.execute(text("ALTER TABLE messages ADD COLUMN body_ko TEXT"))
        if "subject_ko" not in columns:
            conn.execute(text("ALTER TABLE messages ADD COLUMN subject_ko TEXT"))
    logger.info("0045: messages.body_ko / subject_ko added.")
