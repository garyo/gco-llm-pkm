"""Tests for the TickTick tool and client against a mocked TickTick API."""

import json
import logging
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
import requests

from pkm_bridge import http_session, ticktick_client
from pkm_bridge.ticktick_client import TickTickClient
from pkm_bridge.tools import ticktick as ticktick_tool
from pkm_bridge.tools.ticktick import TickTickTool

BASE = TickTickClient.BASE_URL
NY = {"user_timezone": "America/New_York"}

# 10:00 EDT on Monday 2026-09-28 (14:00 UTC).
NOW = datetime(2026, 9, 28, 14, 0, tzinfo=ZoneInfo("UTC"))


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def _task(task_id, title, due=None, all_day=False, project="p-work"):
    task = {"id": task_id, "title": title, "projectId": project, "status": 0}
    if due:
        task.update(dueDate=due, isAllDay=all_day, timeZone="America/New_York")
    return task


class FakeTickTick:
    """Routes session requests to canned responses; records every request."""

    def __init__(self):
        self.routes: dict[tuple[str, str], list[tuple[int, object]]] = {}
        self.requests: list[tuple[str, str, dict]] = []

    def on(self, method, path, *responses):
        self.routes[(method, BASE + path)] = list(responses)

    def __call__(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        queue = self.routes[(method, url)]
        status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(body).encode()
        response.url = url
        return response


@pytest.fixture
def api(monkeypatch):
    fake = FakeTickTick()
    monkeypatch.setattr(requests.Session, "request", lambda _s, m, u, **kw: fake(m, u, **kw))
    monkeypatch.setattr(http_session.time, "sleep", lambda s: None)
    monkeypatch.setattr(ticktick_client, "datetime", FrozenDatetime)
    monkeypatch.setattr(ticktick_tool, "datetime", FrozenDatetime)
    monkeypatch.delenv("TICKTICK_INBOX_ID", raising=False)
    fake.on(
        "GET",
        "/project",
        (200, [{"id": "p-work", "name": "Work"}, {"id": "p-home", "name": "Home"}]),
    )
    fake.on("GET", "/project/p-home/data", (200, {"tasks": []}))
    return fake


@pytest.fixture
def tool(monkeypatch):
    tool = TickTickTool(logging.getLogger("test"), oauth_handler=MagicMock())
    client = TickTickClient("token")
    monkeypatch.setattr(tool, "get_client", lambda: client)
    return tool


class TestCreate:
    def test_create_puts_task_in_named_project_and_returns_its_id(self, api, tool):
        api.on(
            "POST",
            "/task",
            (200, _task("new123", "Call dentist", "2026-09-29T19:00:00.000+0000")),
        )
        result = tool.execute(
            {
                "action": "create",
                "title": "Call dentist",
                "project": "work",
                "due_date": "2026-09-29T15:00:00",
            },
            NY,
        )
        body = next(kw["json"] for m, _, kw in api.requests if m == "POST")
        assert body["projectId"] == "p-work"
        assert body["dueDate"] == "2026-09-29T19:00:00.000+0000"  # 3pm EDT
        assert "{ticktick:new123}" in result
        assert "[Work]" in result
        assert "Due: 2026-09-29 15:00" in result

    def test_create_without_context_timezone_uses_configured_zone(self, api, tool, monkeypatch):
        monkeypatch.setenv("TIMEZONE", "America/Los_Angeles")
        api.on("POST", "/task", (200, _task("t1", "x", project="inbox1")))
        tool.execute({"action": "create", "title": "x", "due_date": "2026-09-29T15:00:00"}, {})
        body = next(kw["json"] for m, _, kw in api.requests if m == "POST")
        assert body["dueDate"] == "2026-09-29T22:00:00.000+0000"  # 3pm PDT
        assert body["timeZone"] == "America/Los_Angeles"


class TestLocalDates:
    def test_timed_task_shows_local_date_and_time(self, api, tool):
        # 9pm EDT on 9/28 is 01:00 UTC on 9/29
        api.on(
            "GET",
            "/project/p-work/data",
            (200, {"tasks": [_task("a", "Evening call", "2026-09-29T01:00:00.000+0000")]}),
        )
        result = tool.execute({"action": "list_all"}, NY)
        assert "Due: 2026-09-28 21:00" in result

    def test_all_day_task_shows_date_only(self, api, tool):
        api.on(
            "GET",
            "/project/p-work/data",
            (200, {"tasks": [_task("a", "Taxes", "2026-09-30T04:00:00.000+0000", True)]}),
        )
        result = tool.execute({"action": "list_all"}, NY)
        assert "Taxes  - Due: 2026-09-30 {ticktick:a}" in result

    def test_today_and_overdue_use_local_date(self, api, tool):
        tonight = _task("tonight", "Tonight", "2026-09-29T01:00:00.000+0000")  # 9/28 9pm
        last_night = _task("last", "Last night", "2026-09-28T02:00:00.000+0000")  # 9/27 10pm
        tomorrow = _task("tmrw", "Tomorrow", "2026-09-29T14:00:00.000+0000")  # 9/29 10am
        api.on("GET", "/project/p-work/data", (200, {"tasks": [tonight, last_night, tomorrow]}))

        today = tool.execute({"action": "list_today"}, NY)
        assert "Tonight" in today and "Last night" in today
        assert "Tomorrow" not in today

        overdue = tool.execute({"action": "list_overdue"}, NY)
        assert "Last night" in overdue
        assert "Tonight" not in overdue


class TestPartialResults:
    def test_failed_project_is_reported_not_hidden(self, api, tool):
        api.on("GET", "/project/p-work/data", (500, {}))
        api.on(
            "GET",
            "/project/p-home/data",
            (200, {"tasks": [_task("h", "Water plants", project="p-home")]}),
        )
        result = tool.execute({"action": "list_all"}, NY)
        assert "Water plants" in result
        assert "failed to load: Work: HTTP 500" in result
        work_fetches = [u for m, u, _ in api.requests if u.endswith("/p-work/data")]
        assert len(work_fetches) == 2  # retried once

    def test_partial_fetch_is_not_cached(self, api):
        client = TickTickClient("token")
        api.on("GET", "/project/p-work/data", (500, {}), (500, {}), (200, {"tasks": []}))
        client.list_tasks()
        assert client.failed_projects
        client.list_tasks()
        assert client.failed_projects == []

    def test_empty_search_still_reports_failures(self, api, tool):
        api.on("GET", "/project/p-work/data", (429, {}))
        result = tool.execute({"action": "search", "query": "anything"}, NY)
        assert result.startswith("No tasks found")
        assert "Work: HTTP 429" in result
