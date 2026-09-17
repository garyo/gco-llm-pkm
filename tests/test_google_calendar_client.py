"""Tests for calendar paging: a busy range must never hide its newest events."""

from datetime import datetime, timedelta, timezone

import pytest

from pkm_bridge.google_calendar_client import EventList, GoogleCalendarClient


class FakeEventsResource:
    """Stands in for service.events(), paging like the real API.

    The real API returns events oldest-first and caps a page at maxResults,
    which is what makes a naive single-page read drop the newest events.
    """

    def __init__(self, events):
        self.events = events
        self.calls = []

    def list(self, **params):
        self.calls.append(params)
        page_size = params["maxResults"]
        start = int(params.get("pageToken") or 0)

        matched = self.events
        if params.get("q"):
            matched = [e for e in matched if params["q"].lower() in e["summary"].lower()]

        page = matched[start : start + page_size]
        result = {"items": page}
        if start + page_size < len(matched):
            result["nextPageToken"] = str(start + page_size)

        return FakeRequest(result)


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeService:
    def __init__(self, events):
        self.resource = FakeEventsResource(events)

    def events(self):
        return self.resource


def make_events(count, *, haircut_at=None):
    """Build `count` daily events, oldest first, optionally one named Haircut."""
    day = datetime(2026, 7, 8, tzinfo=timezone.utc)
    events = []
    for i in range(count):
        summary = "Haircut, Mario" if i == haircut_at else f"Event {i}"
        events.append(
            {
                "id": f"e{i}",
                "summary": summary,
                "start": {"dateTime": (day + timedelta(days=i)).isoformat()},
            }
        )
    return events


@pytest.fixture
def client():
    """Build a client wired to fake events, skipping OAuth setup."""

    def build_client(events):
        c = object.__new__(GoogleCalendarClient)
        c.service = FakeService(events)
        return c

    return build_client


class TestPaging:
    def test_reads_past_a_single_page(self, client):
        c = client(make_events(98))
        result = c.get_events(max_results=50)

        assert result.total == 98
        assert len(result.events) == 50
        assert result.truncated

    def test_keeps_newest_and_drops_oldest(self, client):
        """The regression: a haircut late in a busy range must still be seen."""
        c = client(make_events(98, haircut_at=79))
        result = c.get_events(max_results=50)

        assert "Haircut, Mario" in [e["summary"] for e in result.events]
        assert result.events[0]["summary"] == "Event 97"

    def test_untruncated_range_reports_no_truncation(self, client):
        c = client(make_events(20))
        result = c.get_events(max_results=50)

        assert result.total == 20
        assert len(result.events) == 20
        assert not result.truncated

    def test_empty_range(self, client):
        result = client([]).get_events(max_results=50)

        assert result == EventList([], 0)
        assert not result.truncated


class TestOrdering:
    def test_newest_first_by_default(self, client):
        result = client(make_events(5)).get_events()

        assert [e["summary"] for e in result.events] == [f"Event {i}" for i in (4, 3, 2, 1, 0)]

    def test_oldest_first_on_request(self, client):
        result = client(make_events(5)).get_events(newest_first=False)

        assert [e["summary"] for e in result.events] == [f"Event {i}" for i in range(5)]

    def test_agenda_helpers_stay_chronological(self, client):
        c = client(make_events(5))
        assert c.get_today_events().events == c.get_events(newest_first=False).events


class TestSearch:
    def test_returns_most_recent_matches(self, client):
        """Searching a long history must surface recent hits, not the oldest."""
        events = make_events(300)
        for i in (5, 250, 299):
            events[i]["summary"] = "Haircut, Mario"

        result = client(events).search_events("haircut", max_results=2)

        assert result.total == 3
        assert [e["id"] for e in result.events] == ["e299", "e250"]

    def test_passes_time_bounds_to_api(self, client):
        c = client(make_events(5))
        c.search_events(
            "haircut",
            time_min=datetime(2026, 8, 1, tzinfo=timezone.utc),
            time_max=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

        params = c.service.resource.calls[-1]
        assert params["timeMin"] == "2026-08-01T00:00:00Z"
        assert params["timeMax"] == "2026-09-01T00:00:00Z"
        assert params["q"] == "haircut"
