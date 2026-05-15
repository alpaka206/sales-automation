"""Tests for outbound message scheduling by country."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.agents.outbound.agent import OutboundAgent
from src.agents.outbound.sources.base import ProspectCandidate
from src.db.models import CountrySendWindow


def _make_window(
    country_code: str,
    tz: str,
    hours_start: int,
    hours_end: int,
    avoid_days: list[int] | None = None,
) -> CountrySendWindow:
    """Build a fake CountrySendWindow."""
    w = MagicMock(spec=CountrySendWindow)
    w.country_code = country_code
    w.timezone = tz
    w.hours_start = hours_start
    w.hours_end = hours_end
    w.avoid_days_of_week = avoid_days or [5, 6]
    return w


def _mock_get_window(windows: dict[str, CountrySendWindow]):
    """Return a mock _get_window that looks up from a dict."""

    def _get(code: str):
        return windows.get(code.upper(), windows.get("default"))

    return _get


def test_us_candidate_scheduled_in_us_hours() -> None:
    """US candidate at KR midnight → scheduled for next US business hours."""
    from src.agents.scheduler import compute_next_send_time

    us_window = _make_window("US", "America/New_York", 9, 11, [5, 6])
    default_window = _make_window("default", "UTC", 9, 17, [5, 6])

    with patch(
        "src.agents.scheduler._get_window",
        side_effect=_mock_get_window({"US": us_window, "default": default_window}),
    ):
        # Wednesday 2026-05-20 00:00 UTC = Tuesday 8:00 PM ET
        now = datetime(2026, 5, 20, 0, 0, tzinfo=timezone.utc)
        result = compute_next_send_time("US", now_utc=now)

        from zoneinfo import ZoneInfo

        et = result.astimezone(ZoneInfo("America/New_York"))
        assert et.hour == 9
        assert et.weekday() == 2  # Wednesday


def test_kr_candidate_scheduled_in_kr_hours() -> None:
    """KR candidate → scheduled for KR business hours."""
    from src.agents.scheduler import compute_next_send_time

    kr_window = _make_window("KR", "Asia/Seoul", 9, 11, [5, 6])

    with patch(
        "src.agents.scheduler._get_window",
        side_effect=_mock_get_window({"KR": kr_window}),
    ):
        # Monday 2026-05-18 23:00 UTC = Tuesday 08:00 KST
        now = datetime(2026, 5, 18, 23, 0, tzinfo=timezone.utc)
        result = compute_next_send_time("KR", now_utc=now)

        from zoneinfo import ZoneInfo

        kst = result.astimezone(ZoneInfo("Asia/Seoul"))
        assert kst.hour == 9
        assert kst.weekday() in (0, 1, 2, 3, 4)  # weekday


def test_kr_sunday_skips_to_monday() -> None:
    """KR on Sunday → skip to Monday 9 AM KST."""
    from src.agents.scheduler import compute_next_send_time

    kr_window = _make_window("KR", "Asia/Seoul", 9, 11, [5, 6])

    with patch(
        "src.agents.scheduler._get_window",
        side_effect=_mock_get_window({"KR": kr_window}),
    ):
        # Sunday 2026-05-17 00:00 UTC = Sunday 09:00 KST
        now = datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc)
        result = compute_next_send_time("KR", now_utc=now)

        from zoneinfo import ZoneInfo

        kst = result.astimezone(ZoneInfo("Asia/Seoul"))
        assert kst.weekday() == 0  # Monday
        assert kst.hour == 9


def test_unknown_country_uses_default() -> None:
    """Unknown country code → uses 'default' window."""
    from src.agents.scheduler import compute_next_send_time

    default_window = _make_window("default", "UTC", 9, 17, [5, 6])

    with patch(
        "src.agents.scheduler._get_window",
        side_effect=_mock_get_window({"default": default_window}),
    ):
        now = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)  # Monday
        result = compute_next_send_time("ZZ", now_utc=now)
        assert result.hour == 10


def test_no_window_returns_now() -> None:
    """No window data at all → return now."""
    from src.agents.scheduler import compute_next_send_time

    with patch("src.agents.scheduler._get_window", return_value=None):
        now = datetime(2026, 5, 19, 3, 0, tzinfo=timezone.utc)
        result = compute_next_send_time("XY", now_utc=now)
        assert result == now


def test_persist_message_sets_scheduled_at() -> None:
    """_persist_message should set scheduled_at based on candidate country."""
    from datetime import datetime, timezone

    agent = OutboundAgent.__new__(OutboundAgent)
    agent.llm = MagicMock()

    candidate = ProspectCandidate(
        name="Test Corp",
        email="test@example.kr",
        company="Test Corp",
        domain="example.kr",
        country="KR",
        source="manual_csv",
    )

    mock_draft = MagicMock()
    mock_draft.subject = "Test Subject"
    mock_draft.body = "Test Body"
    mock_draft.language = "ko"

    scheduled_time = datetime(2026, 5, 19, 0, 0, tzinfo=timezone.utc)

    with (
        patch("src.agents.outbound.agent.compute_next_send_time", return_value=scheduled_time),
        patch("src.agents.outbound.agent.SessionLocal") as mock_session_cls,
    ):
        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = None
        mock_prospect = MagicMock()
        mock_prospect.id = 1

        msg = agent._persist_message(
            mock_session, mock_prospect, candidate, mock_draft, score=75
        )

        add_calls = mock_session.add.call_args_list
        msg_obj = add_calls[-1][0][0]
        assert msg_obj.scheduled_at == scheduled_time
