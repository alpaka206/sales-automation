"""Add an owner token so stale inbound workers cannot overwrite a reclaimed job."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def up(engine: Engine) -> None:
    if "inbound_jobs" not in set(inspect(engine).get_table_names()):
        return
    columns = {item["name"] for item in inspect(engine).get_columns("inbound_jobs")}
    if "locked_by" in columns:
        return
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE inbound_jobs ADD COLUMN locked_by VARCHAR(64)")
        )
