"""Tests for the scheduled task system.

Tests repository CRUD, compute_next_run for cron/interval,
budget enforcement, and the ScheduleTaskTool actions.
All tests run without a real database connection (mocked).
"""

import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def logger():
    return logging.getLogger("test")


# ---------------------------------------------------------------------------
# _parse_interval
# ---------------------------------------------------------------------------

from pkm_bridge.scheduler.repository import _parse_interval


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("4h", timedelta(hours=4)),
        ("30m", timedelta(minutes=30)),
        ("1d", timedelta(days=1)),
        ("60s", timedelta(seconds=60)),
        ("12h", timedelta(hours=12)),
    ],
)
def test_parse_interval_valid(expr, expected):
    assert _parse_interval(expr) == expected


@pytest.mark.parametrize(
    "expr",
    ["", "abc", "4x", "h4", "4 hours"],
)
def test_parse_interval_invalid(expr):
    with pytest.raises(ValueError):
        _parse_interval(expr)


# ---------------------------------------------------------------------------
# compute_next_run
# ---------------------------------------------------------------------------

from zoneinfo import ZoneInfo

from pkm_bridge.scheduler.repository import ScheduledTaskRepository, compute_next_run

UTC = ZoneInfo("UTC")
NEW_YORK = ZoneInfo("America/New_York")


def _make_task(**kwargs):
    """Create a mock ScheduledTask with sensible defaults."""
    task = MagicMock()
    task.schedule_type = kwargs.get("schedule_type", "interval")
    task.schedule_expr = kwargs.get("schedule_expr", "4h")
    task.last_run_at = kwargs.get("last_run_at", None)
    task.created_at = kwargs.get("created_at", datetime(2025, 1, 1, 0, 0))
    task.next_run_at = kwargs.get("next_run_at", None)
    return task


def test_compute_next_run_interval_no_last_run():
    task = _make_task(
        schedule_type="interval",
        schedule_expr="4h",
        created_at=datetime(2025, 1, 1, 0, 0),
    )
    now = datetime(2025, 1, 1, 1, 0)
    result = compute_next_run(task, after=now)
    assert result == datetime(2025, 1, 1, 4, 0)


def test_compute_next_run_interval_with_last_run():
    task = _make_task(
        schedule_type="interval",
        schedule_expr="30m",
        last_run_at=datetime(2025, 1, 1, 10, 0),
        created_at=datetime(2025, 1, 1, 0, 0),
    )
    now = datetime(2025, 1, 1, 10, 5)
    result = compute_next_run(task, after=now)
    assert result == datetime(2025, 1, 1, 10, 30)


def test_compute_next_run_interval_multiple_ticks_past():
    """If we're way past the last run, skip forward to the next future tick."""
    task = _make_task(
        schedule_type="interval",
        schedule_expr="1h",
        last_run_at=datetime(2025, 1, 1, 0, 0),
        created_at=datetime(2025, 1, 1, 0, 0),
    )
    now = datetime(2025, 1, 1, 5, 30)
    result = compute_next_run(task, after=now)
    assert result == datetime(2025, 1, 1, 6, 0)


def test_compute_next_run_cron():
    """Cron '0 9 * * *' should give next 9am."""
    task = _make_task(schedule_type="cron", schedule_expr="0 9 * * *")
    now = datetime(2025, 6, 15, 10, 0)
    result = compute_next_run(task, after=now, tz=UTC)
    assert result == datetime(2025, 6, 16, 9, 0)


def test_compute_next_run_cron_weekdays():
    """'0 9 * * 1-5' should skip weekend."""
    task = _make_task(schedule_type="cron", schedule_expr="0 9 * * 1-5")
    # Friday 10am — next is Monday 9am
    now = datetime(2025, 6, 13, 10, 0)  # Friday
    result = compute_next_run(task, after=now, tz=UTC)
    assert result.weekday() == 0  # Monday
    assert result.hour == 9


def test_compute_next_run_cron_is_local_time():
    """Cron fields are local wall time; the result is naive UTC (9am EDT = 13:00 UTC)."""
    task = _make_task(schedule_type="cron", schedule_expr="0 9 * * 1-5")
    now = datetime(2025, 6, 13, 10, 0)  # Friday 6am EDT
    result = compute_next_run(task, after=now, tz=NEW_YORK)
    assert result == datetime(2025, 6, 13, 13, 0)


def test_compute_next_run_cron_follows_dst():
    """Sunday 5pm stays 5pm local when DST ends (Nov 1, 2026): 21:00 UTC, then 22:00."""
    task = _make_task(schedule_type="cron", schedule_expr="0 17 * * 0")
    before_change = compute_next_run(task, after=datetime(2026, 10, 20), tz=NEW_YORK)
    assert before_change == datetime(2026, 10, 25, 21, 0)
    after_change = compute_next_run(task, after=before_change, tz=NEW_YORK)
    assert after_change == datetime(2026, 11, 1, 22, 0)


def test_compute_next_run_cron_defaults_to_configured_timezone(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "America/Los_Angeles")
    task = _make_task(schedule_type="cron", schedule_expr="0 7 * * *")
    result = compute_next_run(task, after=datetime(2025, 6, 13, 12, 0))
    assert result == datetime(2025, 6, 13, 14, 0)  # 7am PDT


def test_mark_run_interval_ticks_from_scheduled_slot():
    """A 12h task due at 07:00 that finishes at 07:03 is next due at 19:00, not 19:03."""
    task = _make_task(
        schedule_type="interval",
        schedule_expr="12h",
        last_run_at=datetime(2025, 1, 1, 19, 2),
        next_run_at=datetime(2025, 1, 2, 7, 0),
    )
    with patch("pkm_bridge.scheduler.repository.datetime") as mock_dt:
        mock_dt.utcnow.return_value = datetime(2025, 1, 2, 7, 3)
        ScheduledTaskRepository.mark_run(MagicMock(), task)
    assert task.last_run_at == datetime(2025, 1, 2, 7, 3)
    assert task.next_run_at == datetime(2025, 1, 2, 19, 0)


def test_mark_run_before_slot_keeps_schedule():
    """Running a task by hand ahead of its slot doesn't move the slot."""
    slot = datetime(2025, 1, 2, 19, 0)
    task = _make_task(schedule_type="interval", schedule_expr="12h", next_run_at=slot)
    with patch("pkm_bridge.scheduler.repository.datetime") as mock_dt:
        mock_dt.utcnow.return_value = datetime(2025, 1, 2, 10, 0)
        ScheduledTaskRepository.mark_run(MagicMock(), task)
    assert task.next_run_at == slot


def test_compute_next_run_unknown_type():
    task = _make_task(schedule_type="unknown", schedule_expr="*")
    with pytest.raises(ValueError, match="Unknown schedule_type"):
        compute_next_run(task, after=datetime(2025, 1, 1))


# ---------------------------------------------------------------------------
# ScheduleTaskTool actions
# ---------------------------------------------------------------------------

from pkm_bridge.tools.schedule_task import ScheduleTaskTool


@pytest.fixture
def tool(logger):
    return ScheduleTaskTool(logger)


def test_tool_name(tool):
    assert tool.name == "schedule_task"


def test_tool_schema_has_action(tool):
    schema = tool.input_schema
    assert "action" in schema["properties"]
    assert set(schema["properties"]["action"]["enum"]) == {
        "create",
        "list",
        "update",
        "delete",
        "toggle",
    }


def test_tool_unknown_action(tool):
    result = tool.execute({"action": "unknown"})
    assert "Unknown action" in result


@patch("pkm_bridge.tools.schedule_task.ScheduleTaskTool._list")
def test_tool_list_calls_list(mock_list, tool):
    mock_list.return_value = "task list"
    result = tool.execute({"action": "list"})
    assert result == "task list"
    mock_list.assert_called_once()


def test_tool_create_missing_fields(tool):
    result = tool.execute({"action": "create", "name": "test"})
    assert "Error" in result
    assert "requires" in result.lower()


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

from pkm_bridge.scheduler.heartbeat import DEFAULT_HEARTBEAT_PROMPT, load_heartbeat_prompt


def test_load_heartbeat_prompt_missing(tmp_path):
    result = load_heartbeat_prompt(tmp_path)
    assert result is None


def test_load_heartbeat_prompt_exists(tmp_path):
    pkm_dir = tmp_path / ".pkm"
    pkm_dir.mkdir()
    heartbeat_file = pkm_dir / "heartbeat.md"
    heartbeat_file.write_text("Check my calendar and notes.", encoding="utf-8")
    result = load_heartbeat_prompt(tmp_path)
    assert result == "Check my calendar and notes."


def test_load_heartbeat_prompt_empty_file(tmp_path):
    pkm_dir = tmp_path / ".pkm"
    pkm_dir.mkdir()
    heartbeat_file = pkm_dir / "heartbeat.md"
    heartbeat_file.write_text("", encoding="utf-8")
    result = load_heartbeat_prompt(tmp_path)
    assert result is None


def test_default_heartbeat_prompt_is_nonempty():
    assert len(DEFAULT_HEARTBEAT_PROMPT.strip()) > 20


# ---------------------------------------------------------------------------
# Dispatcher prompt date injection
# ---------------------------------------------------------------------------

from zoneinfo import ZoneInfo

from pkm_bridge.scheduler.dispatcher import prompt_with_date


def test_prompt_with_date_prefixes_current_date():
    from datetime import datetime

    tz = ZoneInfo("America/New_York")
    result = prompt_with_date("Do the thing.", tz)
    first_line, _, rest = result.partition("\n\n")
    assert first_line.startswith("Current date and time: ")
    assert str(datetime.now(tz).year) in first_line
    assert rest == "Do the thing."


def test_prompt_with_date_no_timezone_falls_back_to_local():
    result = prompt_with_date("Task", None)
    assert result.startswith("Current date and time: ")
    assert result.endswith("Task")


# ---------------------------------------------------------------------------
# Budget (reuse existing Budget class)
# ---------------------------------------------------------------------------

from pkm_bridge.self_improvement.budget import Budget


def test_budget_can_continue():
    b = Budget(max_turns=3, max_input_tokens=1000, max_output_tokens=500)
    assert b.can_continue
    b.record_turn(400, 200)
    assert b.can_continue
    b.record_turn(400, 200)
    assert b.can_continue
    b.record_turn(400, 200)
    assert not b.can_continue  # 3 turns used


def test_budget_input_token_limit():
    b = Budget(max_turns=100, max_input_tokens=500, max_output_tokens=100_000)
    b.record_turn(600, 10)
    assert not b.can_continue
    assert "input token" in b.stop_reason


def test_budget_output_token_limit():
    b = Budget(max_turns=100, max_input_tokens=100_000, max_output_tokens=50)
    b.record_turn(10, 60)
    assert not b.can_continue
    assert "output token" in b.stop_reason


# ---------------------------------------------------------------------------
# Executor tool context
# ---------------------------------------------------------------------------

from types import SimpleNamespace

from pkm_bridge.scheduler.executor import TaskExecutor


def test_executor_passes_timezone_and_scheduled_flag(monkeypatch):
    """Scheduled tool calls carry the configured zone and say they're scheduled."""
    monkeypatch.setenv("TIMEZONE", "America/Chicago")
    usage = SimpleNamespace(input_tokens=1, output_tokens=1)
    tool_use = SimpleNamespace(type="tool_use", id="t1", name="ticktick_query", input={})
    client = MagicMock()
    client.complete.side_effect = [
        SimpleNamespace(stop_reason="tool_use", content=[tool_use], usage=usage),
        SimpleNamespace(stop_reason="end_turn", content=[], usage=usage),
    ]
    registry = MagicMock()
    registry.get_anthropic_tools.return_value = []
    registry.execute_tool.return_value = "ok"

    TaskExecutor(client, registry, logging.getLogger("test")).execute(
        "check tasks", model="claude-haiku-4-5"
    )

    context = registry.execute_tool.call_args.kwargs["context"]
    assert context == {"user_timezone": "America/Chicago", "scheduled": True}
