"""Retire the four pipeline stages that no longer exist on either side.

The board now has exactly seven columns, in flow order: new, meeting_link_sent,
negotiation, reminder_sent, won, closed_lost, closed. Four keys are gone:

- ``follow_up_needed`` — the HubSpot "X_Follow Up Needed" stage is being removed, so
  nothing produces this key any more. Open work belongs in ``negotiation``, which is
  the honest "still being worked" column; ``reminder_sent`` would assert a reminder
  mail that may never have been sent.
- ``contracted`` / ``onboarding`` / ``active`` — never real stages. They leaked in from
  the CONTRACT status vocabulary via ``customer_ops.contract_add``, which used to write
  ``profile.pipeline_stage = status``. All three meant "past the contract", so they
  land on ``won``. That route is closed in the same change; a contract now settles
  ``customer_state`` only.

Rewrites data only — both columns are plain strings with no CHECK constraint or enum
(``conversations.stage`` VARCHAR, ``customer_profiles.pipeline_stage`` VARCHAR(32)), so
no DDL is needed on SQLite or Postgres. Idempotent: re-running matches nothing, which
matters because ``migrate.py`` commits ``up()`` and the tracker row separately and CI
runs ``init_db.py`` twice.

Deliberately does NOT touch ``contacts.lifecycle_stage`` (HubSpot's own vocabulary) or
``customer_profiles.customer_state``, whose default "negotiation" collides textually
with a pipeline key but is a different field.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

# Retired stage key -> the kept stage that carries the same meaning.
STAGE_MAPPING = {
    "follow_up_needed": "negotiation",
    "contracted": "won",
    "onboarding": "won",
    "active": "won",
}

# (table, stage column) pairs. These are the only two stage columns in the schema.
_COLUMNS = (
    ("conversations", "stage"),
    ("customer_profiles", "pipeline_stage"),
)


def up(engine: Engine) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column in _COLUMNS:
            if table not in tables:
                logger.info("0040: %s absent, skipping", table)
                continue
            for old, new in STAGE_MAPPING.items():
                result = conn.execute(
                    text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old"),
                    {"new": new, "old": old},
                )
                if result.rowcount:
                    logger.info(
                        "0040: %s.%s %s -> %s (%s rows)",
                        table, column, old, new, result.rowcount,
                    )
