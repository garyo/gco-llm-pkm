"""Tests for recovering from replies cut off at max_tokens."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from pkm_bridge.history_manager import CUT_OFF_NOTE, drop_unanswered_tool_uses
from pkm_bridge.llm import recover_from_max_tokens
from pkm_bridge.scheduler.executor import TaskExecutor


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool_use(tool_id, name="write_memory"):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input={"x": 1})


def _response(stop_reason, *blocks):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=list(blocks),
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )


def test_drop_unanswered_tool_use_at_end_of_history():
    history = [
        {"role": "user", "content": "rewrite my page"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Writing it now."},
                {"type": "tool_use", "id": "t1", "name": "write_file", "input": {}},
            ],
        },
    ]
    assert drop_unanswered_tool_uses(history) == 1
    assert history[1]["content"] == [{"type": "text", "text": "Writing it now."}]


def test_answered_tool_uses_are_kept():
    history = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "search_notes", "input": {}}],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": ""}]},
    ]
    assert drop_unanswered_tool_uses(history) == 0
    assert history[1]["content"][0]["id"] == "t1"


def test_tool_use_only_message_gets_placeholder():
    history = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}],
        },
        {"role": "user", "content": "next question"},
    ]
    assert drop_unanswered_tool_uses(history) == 1
    assert history[1]["content"] == [{"type": "text", "text": CUT_OFF_NOTE}]


def test_recover_from_max_tokens_drops_calls_and_explains():
    response = _response("max_tokens", _text("Now let me rewrite:"), _tool_use("t1"))
    assistant, notice = recover_from_max_tokens(response, 16000)
    assert assistant == {
        "role": "assistant",
        "content": [{"type": "text", "text": "Now let me rewrite:"}],
    }
    note = notice["content"][0]["text"]
    assert "16000-token output limit" in note and "NOT run: write_memory" in note


def _executor(responses):
    client = MagicMock()
    client.complete.side_effect = responses
    registry = MagicMock()
    registry.get_anthropic_tools.return_value = []
    registry.execute_tool.return_value = "ok"
    return TaskExecutor(client, registry, logging.getLogger("test")), client, registry


def test_executor_continues_after_cut_off_reply():
    executor, client, registry = _executor(
        [
            _response("max_tokens", _text("Drafting"), _tool_use("t1")),
            _response("end_turn", _text("Filed it in two parts.")),
        ]
    )
    result = executor.execute("do the thing", model="claude-haiku-4-5")
    assert result["error"] is None
    assert result["summary"] == "Filed it in two parts."
    registry.execute_tool.assert_not_called()  # the truncated call never ran
    second_call_messages = client.complete.call_args_list[1].kwargs["messages"]
    assert "cut off" in second_call_messages[-1]["content"][0]["text"]


def test_executor_reports_error_when_budget_ends_on_cut_off_reply():
    executor, _, _ = _executor([_response("max_tokens", _text("Drafting"))])
    result = executor.execute("do the thing", model="claude-haiku-4-5", max_turns=1)
    assert result["error"] and "max_tokens" in result["error"]
