"""Tests for the Google Calendar tool's date-range handling."""

import logging
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from pkm_bridge.google_calendar_client import EventList
from pkm_bridge.tools.google_calendar import GoogleCalendarTool

NY = ZoneInfo("America/New_York")


@pytest.fixture
def tool():
    return GoogleCalendarTool(logging.getLogger("test"))


class TestParseTimeBounds:
    def test_date_only_max_covers_that_whole_day(self, tool):
        lo, hi = tool.parse_time_bounds(
            {"time_min": "2026-09-27", "time_max": "2026-09-27"}, "America/New_York"
        )
        assert lo == datetime(2026, 9, 27, tzinfo=NY)
        assert hi == datetime(2026, 9, 28, tzinfo=NY)

    def test_datetime_max_is_taken_as_given(self, tool):
        _, hi = tool.parse_time_bounds({"time_max": "2026-09-27T15:30:00"}, "America/New_York")
        assert hi == datetime(2026, 9, 27, 15, 30, tzinfo=NY)

    def test_explicit_offset_is_kept(self, tool):
        _, hi = tool.parse_time_bounds({"time_max": "2026-09-27T15:30:00+00:00"}, "Asia/Tokyo")
        assert hi.utcoffset().total_seconds() == 0

    def test_unknown_zone_falls_back_to_configured(self, tool, monkeypatch):
        monkeypatch.setenv("TIMEZONE", "Europe/London")
        lo, _ = tool.parse_time_bounds({"time_min": "2026-09-27"}, "Not/AZone")
        assert lo.tzinfo == ZoneInfo("Europe/London")


def test_list_range_for_a_single_day_asks_for_that_day(tool, monkeypatch):
    client = MagicMock()
    client.get_events.return_value = EventList(events=[], total=0)
    monkeypatch.setattr(tool, "get_client", lambda: client)
    monkeypatch.setenv("TIMEZONE", "America/New_York")

    tool.execute({"action": "list_range", "time_min": "2026-09-27", "time_max": "2026-09-27"})

    kwargs = client.get_events.call_args.kwargs
    assert kwargs["time_min"] == datetime(2026, 9, 27, tzinfo=NY)
    assert kwargs["time_max"] == datetime(2026, 9, 28, tzinfo=NY)
