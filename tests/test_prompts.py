"""Tests for system prompt rendering and the MCP prompt context."""

from datetime import datetime, timedelta

import pytest

import mcp_server.tools as mcp_tools
from config.settings import Config

PROMPTS = ("system_prompt.txt", "system_prompt_mcp.txt")


@pytest.fixture
def config(tmp_path, monkeypatch):
    org = tmp_path / "org"
    (org / "journals").mkdir(parents=True)
    env_file = tmp_path / ".env"
    env_file.write_text("")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("ORG_DIR", str(org))
    monkeypatch.setenv("LOGSEQ_DIR", str(tmp_path / "logseq"))
    monkeypatch.setenv("TIMEZONE", "America/New_York")
    monkeypatch.setenv("EDITOR_BASE_URL", "https://editor.test")
    return Config(env_file=str(env_file))


@pytest.mark.parametrize("name", PROMPTS)
def test_render_fills_placeholders_and_drops_maintainer_comment(config, name):
    text = config.render_prompt(name)
    assert not text.startswith("<!--")
    assert str(config.org_dir) in text
    for leftover in ("{ORG_DIR}", "{LOGSEQ_DIR}", "{EDITOR_BASE_URL}", "{{", "}}"):
        assert leftover not in text


@pytest.mark.parametrize("name", PROMPTS)
def test_prompts_carry_the_shared_note_rules(config, name):
    text = config.render_prompt(name)
    for rule in ("journal_append", "edit_note", "note_to_self", "## Heartbeat", "read-only"):
        assert rule in text
    for stale in ("create-org-journal", "add_journal_note", "two months", "PRIMARY"):
        assert stale not in text


def test_flat_system_prompt_includes_user_context(config):
    flat = config.get_system_prompt(user_context="Gary plays bass.")
    assert flat.startswith("You are Gary's assistant")
    assert "# USER CONTEXT\n\nGary plays bass." in flat


def test_prompt_context_lists_recent_journals_by_path(config, monkeypatch):
    def no_db():
        raise RuntimeError("no database in tests")

    monkeypatch.setattr("pkm_bridge.database.init_db", no_db)
    monkeypatch.setattr(mcp_tools, "_config", config)
    today = datetime.now(config.timezone).date()
    old = (today - timedelta(days=5)).isoformat()
    for day in (today.isoformat(), old):
        (config.org_dir / "journals" / f"{day}.md").write_text(f"# Notes for {day}\n")
    memory = config.org_dir / ".pkm" / "memory"
    memory.mkdir(parents=True)
    (memory / "user-profile.md").write_text("PROFILE-MARKER\n")

    text = mcp_tools.build_prompt_context()
    assert f"## org:journals/{today.isoformat()}.md\n# Notes for {today.isoformat()}" in text
    assert old not in text
    assert "unknown" not in text
    assert "PROFILE-MARKER" not in text
    assert "current local date/time" in text
