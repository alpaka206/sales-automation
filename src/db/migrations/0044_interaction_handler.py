"""Who handled it: ``customer_interactions.handler``.

The manual log started out asking for a *direction* (고객 → 우리 / 우리 → 고객), which was
the wrong question. One record is the whole exchange written up once — the customer asked,
we answered, they came back — so there is no single direction to pick, and the operator was
being made to shred one conversation into rows to answer it.

What is actually worth knowing about a hand-written record is **who was on it**. After the
first reply the thread leaves HubSpot and lands wherever the customer prefers, so months
later "누가 통화했는지" is a question only this column can answer.

``direction`` stays: rows synced from HubSpot and the Message rows rendered on the same
timeline still carry a real one. New manual records store its default, ``note``.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "customer_interactions" not in set(inspector.get_table_names()):
        logger.info("0044: customer_interactions missing; skipping.")
        return
    columns = {column["name"] for column in inspector.get_columns("customer_interactions")}
    if "handler" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE customer_interactions ADD COLUMN handler VARCHAR(120)"))
    logger.info("0044: customer_interactions.handler added.")
