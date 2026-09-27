"""Tests for the prompt-cache layout: stable system prompt, time note in the user turn."""

import re

from pkm_bridge.history_manager import (
    TIME_NOTE_PREFIX,
    mark_cache_breakpoints,
    user_visible_text,
)


def _user(text, note="[Current date/time: Sunday, September 27, 2026, 12:47 EDT]"):
    return {
        "role": "user",
        "content": [{"type": "text", "text": note}, {"type": "text", "text": text}],
    }


def _assistant_tool_use(tool_id):
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_id, "name": "search_notes", "input": {}}],
    }


def _tool_result(tool_id):
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}],
    }


def _breakpoints(messages):
    return [
        (i, j)
        for i, m in enumerate(messages)
        if isinstance(m["content"], list)
        for j, b in enumerate(m["content"])
        if "cache_control" in b
    ]


def test_breakpoints_on_last_message_and_previous_turn_start():
    messages = [
        _user("first question"),
        _assistant_tool_use("t1"),
        _tool_result("t1"),
        {"role": "assistant", "content": [{"type": "text", "text": "answer"}]},
        _user("second question"),
    ]
    mark_cache_breakpoints(messages)
    assert _breakpoints(messages) == [(0, 1), (4, 1)]


def test_breakpoints_move_through_tool_loop():
    messages = [_user("q"), _assistant_tool_use("t1"), _tool_result("t1")]
    mark_cache_breakpoints(messages)
    messages += [_assistant_tool_use("t2"), _tool_result("t2")]
    mark_cache_breakpoints(messages)
    # Old tool-result breakpoint cleared; current turn start + last message marked.
    assert _breakpoints(messages) == [(0, 1), (4, 0)]


def test_breakpoints_never_leak_into_shared_blocks():
    persisted = [_user("q")]
    api_copy = [{**m, "content": list(m["content"])} for m in persisted]
    mark_cache_breakpoints(api_copy)
    assert _breakpoints(api_copy) == [(0, 1)]
    assert _breakpoints(persisted) == []


def test_string_content_is_skipped():
    messages = [{"role": "user", "content": "legacy string message"}]
    mark_cache_breakpoints(messages)
    assert _breakpoints(messages) == []


def test_user_visible_text_hides_time_note():
    assert user_visible_text(_user("hello")["content"]) == "hello"
    assert user_visible_text("plain string") == "plain string"
    assert user_visible_text(_tool_result("t1")["content"]) == ""


def test_time_note_format():
    from config.settings import Config

    config = Config.__new__(Config)  # skip env validation; only .timezone is used
    config.timezone = None
    note = config.current_time_note("America/New_York")
    assert note.startswith(TIME_NOTE_PREFIX)
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}[+-]\d{4}\)\]$", note)
    assert not re.search(r":\d{2}:\d{2}", note)  # minute granularity, no seconds
