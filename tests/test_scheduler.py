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

from pkm_bridge.scheduler.repository import compute_next_run


def _make_task(**kwargs):
    """Create a mock ScheduledTask with sensible defaults."""
    task = MagicMock()
    task.schedule_type = kwargs.get("schedule_type", "interval")
    task.schedule_expr = kwargs.get("schedule_expr", "4h")
    task.last_run_at = kwargs.get("last_run_at", None)
    task.created_at = kwargs.get("created_at", datetime(2025, 1, 1, 0, 0))
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
    result = compute_next_run(task, after=now)
    assert result == datetime(2025, 6, 16, 9, 0)


def test_compute_next_run_cron_weekdays():
    """'0 9 * * 1-5' should skip weekend."""
    task = _make_task(schedule_type="cron", schedule_expr="0 9 * * 1-5")
    # Friday 10am — next is Monday 9am
    now = datetime(2025, 6, 13, 10, 0)  # Friday
    result = compute_next_run(task, after=now)
    assert result.weekday() == 0  # Monday
    assert result.hour == 9


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

from types import SimpleNamespace

from pkm_bridge.models import TokenUsage
from pkm_bridge.scheduler.dispatcher import TaskDispatcher, daily_budget_fraction
from pkm_bridge.scheduler.executor import TaskExecutor
from pkm_bridge.self_improvement.budget import Budget


def test_budget_can_continue():
    b = Budget(max_turns=3, max_input_tokens=1000, max_output_tokens=500)
    assert b.can_continue
    b.record_turn(TokenUsage(input_tokens=300, output_tokens=100))
    assert b.can_continue
    b.record_turn(TokenUsage(input_tokens=300, output_tokens=100))
    assert b.can_continue
    b.record_turn(TokenUsage(input_tokens=300, output_tokens=100))
    assert not b.can_continue  # 3 turns used
    assert "max turns" in b.stop_reason


def test_budget_input_token_limit():
    b = Budget(max_turns=100, max_input_tokens=500, max_output_tokens=100_000)
    b.record_turn(TokenUsage(input_tokens=600, output_tokens=10))
    assert not b.can_continue
    assert "input token" in b.stop_reason


def test_budget_counts_cached_input_at_its_price():
    """A few uncached tokens plus a large cache read must still use up the input budget."""
    b = Budget(max_turns=100, max_input_tokens=10_000, model="claude-sonnet-5")
    b.record_turn(TokenUsage(input_tokens=3, cache_write_tokens=4000))  # 3 + 5000
    assert b.can_continue
    b.record_turn(TokenUsage(input_tokens=3, cache_read_tokens=50_000))  # 3 + 5000
    assert not b.can_continue
    assert b.billable_input_tokens == pytest.approx(10_006)
    assert (b.usage.cache_write_tokens, b.usage.cache_read_tokens) == (4000, 50_000)


def test_budget_output_token_limit():
    b = Budget(max_turns=100, max_input_tokens=100_000, max_output_tokens=50)
    b.record_turn(TokenUsage(input_tokens=10, output_tokens=60))
    assert not b.can_continue
    assert "output token" in b.stop_reason


def test_executor_reports_cache_tokens_and_cost():
    usage = SimpleNamespace(
        input_tokens=5,
        output_tokens=1000,
        cache_creation_input_tokens=2000,
        cache_read_input_tokens=100_000,
    )
    client = MagicMock()
    client.complete.return_value = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Done")], usage=usage
    )
    registry = MagicMock()
    registry.get_anthropic_tools.return_value = []
    executor = TaskExecutor(client, registry, logging.getLogger("test"))

    result = executor.execute("task", model="claude-sonnet-5")

    assert result["error"] is None
    assert result["input_tokens"] == 5
    assert (result["cache_write_tokens"], result["cache_read_tokens"]) == (2000, 100_000)
    # Sonnet 5: 5 x $2 + 2000 x $2.50 + 100k x $0.20 + 1000 x $10, per million.
    assert result["cost_usd"] == pytest.approx(0.03501)


def _daily_usage(cost_usd=0.0, output_tokens=0):
    return SimpleNamespace(cost_usd=cost_usd, output_tokens=output_tokens)


def test_daily_budget_is_the_larger_of_cost_and_output_shares(monkeypatch):
    monkeypatch.setenv("CRON_DAILY_COST_LIMIT_USD", "2.0")
    monkeypatch.setenv("CRON_DAILY_OUTPUT_TOKEN_LIMIT", "1000")
    assert daily_budget_fraction(_daily_usage(1.0, 100)) == pytest.approx(0.5)
    assert daily_budget_fraction(_daily_usage(0.1, 900)) == pytest.approx(0.9)


def test_dispatcher_stops_when_daily_cost_limit_reached(monkeypatch, logger):
    monkeypatch.setenv("CRON_DAILY_COST_LIMIT_USD", "1.0")
    dispatcher = TaskDispatcher(MagicMock(), logger)
    with patch(
        "pkm_bridge.scheduler.dispatcher.DailyTokenUsageRepository.get_today",
        return_value=_daily_usage(cost_usd=1.0, output_tokens=10),
    ):
        assert not dispatcher._check_global_budget(MagicMock())
    with patch(
        "pkm_bridge.scheduler.dispatcher.DailyTokenUsageRepository.get_today",
        return_value=_daily_usage(cost_usd=0.99, output_tokens=10),
    ):
        assert dispatcher._check_global_budget(MagicMock())


def test_schema_upgrade_adds_missing_cost_columns():
    from pkm_bridge.database import _upgrade_schema

    engine = MagicMock()
    conn = MagicMock()
    engine.begin.return_value.__enter__ = MagicMock(return_value=conn)
    engine.begin.return_value.__exit__ = MagicMock(return_value=False)
    inspector = MagicMock()
    inspector.get_table_names.return_value = ["daily_token_usage"]
    inspector.get_columns.return_value = [{"name": "id"}, {"name": "cache_read_tokens"}]

    with patch("pkm_bridge.database.inspect", return_value=inspector):
        _upgrade_schema(engine)

    statements = [str(call.args[0]) for call in conn.execute.call_args_list]
    assert statements == [
        "ALTER TABLE daily_token_usage ADD COLUMN cache_write_tokens INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE daily_token_usage ADD COLUMN cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0",
    ]
