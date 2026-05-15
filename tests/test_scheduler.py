"""Tests for per-country send-time scheduling."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.db.base import Base
from src.db.models import CountrySendWindow
from src.agents import scheduler


def _seed(session: Session) -> None:
    """Insert minimal test windows."""
    rows = [
        CountrySendWindow(
            country_code="KR",
            country_name="South Korea",
            timezone="Asia/Seoul",
            hours_start=9,
            hours_end=11,
            avoid_days_of_week=[5, 6],
        ),
        CountrySendWindow(
            country_code="AE",
            country_name="UAE",
            timezone="Asia/Dubai",
            hours_start=10,
            hours_end=12,
            avoid_days_of_week=[4, 5],
        ),
        CountrySendWindow(
            country_code="default",
            country_name="Default",
            timezone="UTC",
            hours_start=9,
            hours_end=11,
            avoid_days_of_week=[5, 6],
        ),
    ]
    session.add_all(rows)
    session.commit()


@pytest.fixture(autouse=True)
def _db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    _seed(session)
    monkeypatch.setattr(scheduler, "SessionLocal", factory)
    yield session
    session.close()


def test_kr_sunday_midnight_utc_returns_monday_morning() -> None:
    """Sunday 00:00 UTC = Sunday 09:00 KST. Sunday is day 6 (avoided). Next is Monday 09:00 KST."""
    sun_midnight_utc = datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc)  # Sunday
    result = scheduler.compute_next_send_time("KR", sun_midnight_utc)
    assert result.weekday() == 0  # Monday
    assert result.hour == 0  # 09:00 KST = 00:00 UTC
    assert result.minute == 0


def test_kr_weekday_within_window() -> None:
    """If current local time is within the window on a weekday, return now."""
    wed_0030_utc = datetime(2026, 5, 13, 0, 30, tzinfo=timezone.utc)  # Wed 09:30 KST
    result = scheduler.compute_next_send_time("KR", wed_0030_utc)
    assert result == wed_0030_utc


def test_kr_weekday_after_window() -> None:
    """After the window on a weekday, schedule next day's window start."""
    wed_0300_utc = datetime(2026, 5, 13, 3, 0, tzinfo=timezone.utc)  # Wed 12:00 KST
    result = scheduler.compute_next_send_time("KR", wed_0300_utc)
    assert result.day == 14  # Thursday
    assert result.hour == 0  # 09:00 KST = 00:00 UTC


def test_ae_friday_avoided() -> None:
    """UAE avoids Friday (4) and Saturday (5)."""
    fri_0600_utc = datetime(2026, 5, 15, 6, 0, tzinfo=timezone.utc)  # Fri 10:00 Dubai
    result = scheduler.compute_next_send_time("AE", fri_0600_utc)
    assert result.weekday() == 6  # Sunday


def test_fallback_to_default() -> None:
    """Unknown country code falls back to the 'default' row."""
    mon_0900_utc = datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc)  # Mon 09:00 UTC
    result = scheduler.compute_next_send_time("ZZ", mon_0900_utc)
    assert result == mon_0900_utc


def test_migration_creates_18_rows() -> None:
    """The migration module seeds exactly 18 country rows."""
    engine = create_engine("sqlite:///:memory:")
    mod = importlib.import_module("src.db.migrations.0005_country_send_windows")
    mod.up(engine)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM country_send_windows")).scalar()
    assert count == 18


def test_migration_idempotent() -> None:
    """Running the migration twice does not duplicate rows."""
    engine = create_engine("sqlite:///:memory:")
    mod = importlib.import_module("src.db.migrations.0005_country_send_windows")
    mod.up(engine)
    mod.up(engine)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM country_send_windows")).scalar()
    assert count == 18
