"""Create country_send_windows table and seed major countries."""

from __future__ import annotations

import json

from sqlalchemy import Engine, inspect, text

SEED_DATA = [
    ("KR", "South Korea", "Asia/Seoul", 9, 11, [5, 6]),
    ("JP", "Japan", "Asia/Tokyo", 9, 11, [5, 6]),
    ("US", "United States", "America/New_York", 9, 11, [5, 6]),
    ("GB", "United Kingdom", "Europe/London", 9, 11, [5, 6]),
    ("DE", "Germany", "Europe/Berlin", 9, 11, [5, 6]),
    ("FR", "France", "Europe/Paris", 9, 11, [5, 6]),
    ("SG", "Singapore", "Asia/Singapore", 9, 11, [5, 6]),
    ("ID", "Indonesia", "Asia/Jakarta", 9, 11, [5, 6]),
    ("VN", "Vietnam", "Asia/Ho_Chi_Minh", 9, 11, [5, 6]),
    ("TH", "Thailand", "Asia/Bangkok", 9, 11, [5, 6]),
    ("IN", "India", "Asia/Kolkata", 10, 12, [5, 6]),
    ("PH", "Philippines", "Asia/Manila", 9, 11, [5, 6]),
    ("AU", "Australia", "Australia/Sydney", 9, 11, [5, 6]),
    ("BR", "Brazil", "America/Sao_Paulo", 9, 11, [5, 6]),
    ("MX", "Mexico", "America/Mexico_City", 9, 11, [5, 6]),
    ("AE", "UAE", "Asia/Dubai", 10, 12, [4, 5]),
    ("IL", "Israel", "Asia/Jerusalem", 9, 11, [4, 5]),
    ("default", "Default", "UTC", 9, 11, [5, 6]),
]


def up(engine: Engine) -> None:
    """Create table and insert seed rows."""
    insp = inspect(engine)
    if "country_send_windows" not in insp.get_table_names():
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE country_send_windows (
                    country_code VARCHAR PRIMARY KEY,
                    country_name VARCHAR NOT NULL,
                    timezone VARCHAR NOT NULL,
                    hours_start INTEGER NOT NULL,
                    hours_end INTEGER NOT NULL,
                    avoid_days_of_week JSON
                )
            """))

    with engine.begin() as conn:
        for code, name, tz, start, end, avoid in SEED_DATA:
            exists = conn.execute(
                text("SELECT 1 FROM country_send_windows WHERE country_code = :c"),
                {"c": code},
            ).fetchone()
            if not exists:
                conn.execute(
                    text(
                        "INSERT INTO country_send_windows "
                        "(country_code, country_name, timezone, hours_start, hours_end, avoid_days_of_week) "
                        "VALUES (:code, :name, :tz, :start, :end, :avoid)"
                    ),
                    {
                        "code": code,
                        "name": name,
                        "tz": tz,
                        "start": start,
                        "end": end,
                        "avoid": json.dumps(avoid),
                    },
                )
