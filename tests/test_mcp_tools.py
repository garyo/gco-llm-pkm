"""Tests for MCP tool registration: worker threads, paged reads, schedule_task."""

import asyncio
import logging
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from mcp_server import tools as mcp_tools
from mcp_server.tools import _ThreadedTools, register_all_tools
from pkm_bridge.file_editor import FileEditor
from pkm_bridge.tools.schedule_task import ScheduleTaskTool


def _text(result) -> str:
    content, _structured = result
    return content[0].text


def test_sync_tools_run_off_the_event_loop_and_concurrently():
    server = FastMCP("test")
    mcp = _ThreadedTools(server)

    @mcp.tool()
    def slow(label: str) -> str:
        """Sleep, then report the thread."""
        time.sleep(0.3)
        return f"{label}:{threading.current_thread().name}"

    async def main():
        loop_thread = threading.current_thread().name
        started = time.monotonic()
        results = await asyncio.gather(
            server.call_tool("slow", {"label": "a"}), server.call_tool("slow", {"label": "b"})
        )
        return loop_thread, time.monotonic() - started, [_text(r) for r in results]

    loop_thread, elapsed, texts = asyncio.run(main())
    assert elapsed < 0.55  # the two sleeps overlapped
    assert all(not t.endswith(f":{loop_thread}") for t in texts)


def test_threaded_registration_keeps_the_schema():
    plain, threaded = FastMCP("plain"), FastMCP("threaded")

    def tool_fn(path: str, offset: int = 0, tags: list[str] | None = None) -> str:
        """Docstring shown to the model."""
        return path

    plain.tool()(tool_fn)
    _ThreadedTools(threaded).tool()(tool_fn)

    [a], [b] = asyncio.run(plain.list_tools()), asyncio.run(threaded.list_tools())
    assert a.model_dump() == b.model_dump()


@pytest.fixture
def server(tmp_path):
    org = tmp_path / "org"
    org.mkdir()
    (org / "short.org").write_text("tiny file")
    (org / "long.org").write_text("x" * 250_000)
    editor = FileEditor(logging.getLogger("test"), str(org), None)

    server = FastMCP("test")
    with (
        patch.object(mcp_tools, "_get_file_editor", return_value=editor),
        patch.object(mcp_tools, "_log_tool_execution"),
    ):
        register_all_tools(server)
        yield server


def _read(server, **args) -> str:
    return _text(asyncio.run(server.call_tool("read_file", args)))


def test_full_read_has_hash(server):
    text = _read(server, path="org:short.org")
    assert text.startswith("[mtime=")
    assert "hash=" in text.splitlines()[0]
    assert text.endswith("tiny file")


def test_truncated_and_offset_reads_have_no_hash(server):
    first = _read(server, path="org:long.org")
    assert first.startswith("[partial read from offset 0 of 250000 chars")
    assert "hash=" not in first.splitlines()[0]
    assert "offset=200000" in first

    rest = _read(server, path="org:long.org", offset=200_000)
    assert rest.startswith("[partial read from offset 200000 of 250000 chars")
    assert rest.splitlines()[1] == "x" * 50_000


def test_schedule_task_create_keeps_max_turns():
    created = MagicMock()
    created.name, created.id = "t", 1
    created.schedule_type, created.schedule_expr = "interval", "1d"
    with (
        patch("pkm_bridge.database.get_db"),
        patch("pkm_bridge.scheduler.repository.ScheduledTaskRepository") as repo,
    ):
        repo.get_by_name.return_value = None
        repo.create.return_value = created
        ScheduleTaskTool(logging.getLogger("test")).execute(
            {
                "action": "create",
                "name": "t",
                "prompt": "p",
                "schedule_type": "interval",
                "schedule_expr": "1d",
                "max_turns": 25,
            }
        )
    assert repo.create.call_args.kwargs["max_turns"] == 25
