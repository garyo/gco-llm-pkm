"""Tests for the self-improvement agent, meta-tools, budget, and filesystem.

All tests use tmp_path fixtures and mocked DB — no real database or API required.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

from pkm_bridge.self_improvement.budget import Budget


class TestBudget:
    def test_initial_state(self):
        b = Budget(max_turns=5, max_actions=3)
        assert b.can_continue
        assert b.can_act
        assert b.turns_remaining == 5
        assert b.actions_remaining == 3
        assert b.stop_reason is None

    def test_record_turn(self):
        b = Budget(max_turns=2)
        b.record_turn(1000, 500)
        assert b.turns_used == 1
        assert b.input_tokens_used == 1000
        assert b.output_tokens_used == 500
        assert b.can_continue

        b.record_turn(1000, 500)
        assert b.turns_used == 2
        assert not b.can_continue
        assert "max turns" in b.stop_reason

    def test_record_action(self):
        b = Budget(max_actions=2)
        b.record_action()
        assert b.actions_used == 1
        assert b.can_act

        b.record_action()
        assert not b.can_act

    def test_token_cap(self):
        b = Budget(max_input_tokens=100)
        b.record_turn(101, 0)
        assert not b.can_continue
        assert "input token cap" in b.stop_reason

    def test_output_token_cap(self):
        b = Budget(max_output_tokens=100)
        b.record_turn(0, 101)
        assert not b.can_continue
        assert "output token cap" in b.stop_reason

    def test_summary(self):
        b = Budget(max_turns=5, max_actions=3, max_input_tokens=1000, max_output_tokens=500)
        b.record_turn(100, 50)
        b.record_action()
        s = b.summary()
        assert s["turns"] == "1/5"
        assert s["actions"] == "1/3"
        assert s["input_tokens"] == "100/1000"
        assert s["output_tokens"] == "50/500"


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------

from pkm_bridge.self_improvement.filesystem import (
    MEMORY_CATEGORIES,
    ensure_pkm_structure,
    get_memory_dir,
    get_pkm_dir,
    get_runs_dir,
    get_skills_dir,
    read_memory_file,
    write_memory_file,
)


class TestFilesystem:
    def test_ensure_pkm_structure(self, tmp_path: Path):
        pkm_dir = ensure_pkm_structure(tmp_path)
        assert (pkm_dir / "skills").is_dir()
        assert (pkm_dir / "memory").is_dir()
        assert (pkm_dir / "runs").is_dir()

    def test_migration_from_old_skills(self, tmp_path: Path):
        """Skills in .pkm-skills/ should be copied to .pkm/skills/."""
        old_dir = tmp_path / ".pkm-skills"
        old_dir.mkdir()
        (old_dir / "my-skill.md").write_text("# test skill")

        pkm_dir = ensure_pkm_structure(tmp_path)

        # File should be copied
        assert (pkm_dir / "skills" / "my-skill.md").exists()
        assert (pkm_dir / "skills" / "my-skill.md").read_text() == "# test skill"

        # Old dir should now be a symlink
        assert (tmp_path / ".pkm-skills").is_symlink()

    def test_migration_preserves_existing(self, tmp_path: Path):
        """If file already exists in new location, don't overwrite."""
        old_dir = tmp_path / ".pkm-skills"
        old_dir.mkdir()
        (old_dir / "existing.md").write_text("old version")

        # Pre-create the new structure with a different version
        new_skills = tmp_path / ".pkm" / "skills"
        new_skills.mkdir(parents=True)
        (new_skills / "existing.md").write_text("new version")

        ensure_pkm_structure(tmp_path)

        # New version should be preserved
        assert (new_skills / "existing.md").read_text() == "new version"

    def test_memory_read_write(self, tmp_path: Path):
        ensure_pkm_structure(tmp_path)

        # Write
        write_memory_file("observations", "# Test\n- Found something", tmp_path)
        content = read_memory_file("observations", tmp_path)
        assert "Found something" in content

    def test_memory_append(self, tmp_path: Path):
        ensure_pkm_structure(tmp_path)

        write_memory_file("observations", "Line 1", tmp_path)
        write_memory_file("observations", "Line 2", tmp_path, append=True)

        content = read_memory_file("observations", tmp_path)
        assert "Line 1" in content
        assert "Line 2" in content

    def test_memory_read_nonexistent(self, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        assert read_memory_file("observations", tmp_path) == ""

    def test_get_dirs(self, tmp_path: Path):
        assert get_pkm_dir(tmp_path).name == ".pkm"
        assert get_skills_dir(tmp_path).name == "skills"
        assert get_memory_dir(tmp_path).name == "memory"
        assert get_runs_dir(tmp_path).name == "runs"


# ---------------------------------------------------------------------------
# Meta-tools (inspection)
# ---------------------------------------------------------------------------

from pkm_bridge.self_improvement.meta_tools import (
    InspectSkillsTool,
    ReadMemoryTool,
    WriteMemoryTool,
    WriteSkillTool,
    DeleteSkillTool,
    InspectSystemPromptTool,
)
from pkm_bridge.tools.skills import _build_md_frontmatter


@pytest.fixture
def logger():
    return logging.getLogger("test-si")


class TestInspectSkillsTool:
    def _create_skill(self, org_dir: Path, name: str, desc: str = "test"):
        skills_dir = org_dir / ".pkm" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        meta = {"name": name, "description": desc, "tags": [], "use_count": 0, "last_used": "never"}
        fm = _build_md_frontmatter(meta)
        (skills_dir / f"{name}.md").write_text(fm + "\n\ncontent\n")

    def test_list_all(self, logger, tmp_path: Path):
        self._create_skill(tmp_path, "skill-a", "Alpha")
        self._create_skill(tmp_path, "skill-b", "Beta")

        tool = InspectSkillsTool(logger, tmp_path)
        result = tool.execute({})
        parsed = json.loads(result)
        assert len(parsed) == 2
        names = [s["name"] for s in parsed]
        assert "skill-a" in names
        assert "skill-b" in names

    def test_read_specific(self, logger, tmp_path: Path):
        self._create_skill(tmp_path, "my-skill", "My skill desc")

        tool = InspectSkillsTool(logger, tmp_path)
        result = tool.execute({"skill_name": "my-skill"})
        parsed = json.loads(result)
        assert parsed["name"] == "my-skill"
        assert parsed["description"] == "My skill desc"

    def test_empty(self, logger, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        tool = InspectSkillsTool(logger, tmp_path)
        result = tool.execute({})
        assert "No skills found" in result

    def test_not_found(self, logger, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        tool = InspectSkillsTool(logger, tmp_path)
        result = tool.execute({"skill_name": "nonexistent"})
        assert "not found" in result


class TestReadMemoryTool:
    def test_read_all_empty(self, logger, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        tool = ReadMemoryTool(logger, tmp_path)
        result = tool.execute({})
        assert "No memory files" in result

    def test_read_specific(self, logger, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        write_memory_file("observations", "# Test observation", tmp_path)

        tool = ReadMemoryTool(logger, tmp_path)
        result = tool.execute({"category": "observations"})
        assert "Test observation" in result

    def test_invalid_category(self, logger, tmp_path: Path):
        tool = ReadMemoryTool(logger, tmp_path)
        result = tool.execute({"category": "invalid"})
        assert "Invalid category" in result


class TestInspectSystemPromptTool:
    def test_reads_file(self, logger, tmp_path: Path):
        prompt_file = tmp_path / "system_prompt.txt"
        prompt_file.write_text("You are a helpful assistant.")

        tool = InspectSystemPromptTool(logger, prompt_file)
        result = tool.execute({})
        assert "You are a helpful assistant" in result

    def test_missing_file(self, logger, tmp_path: Path):
        tool = InspectSystemPromptTool(logger, tmp_path / "nonexistent.txt")
        result = tool.execute({})
        assert "Error" in result


# ---------------------------------------------------------------------------
# Meta-tools (action)
# ---------------------------------------------------------------------------

class TestWriteSkillTool:
    def test_create_recipe(self, logger, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        tool = WriteSkillTool(logger, tmp_path)
        result = tool.execute({
            "skill_name": "new-skill",
            "skill_type": "recipe",
            "description": "A new skill",
            "content": "## Steps\n1. Do it",
            "tags": ["test"],
        })
        assert "Created" in result
        assert (tmp_path / ".pkm" / "skills" / "new-skill.md").exists()
        assert len(tool._run_log) == 1

    def test_bad_name(self, logger, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        tool = WriteSkillTool(logger, tmp_path)
        result = tool.execute({
            "skill_name": "BAD NAME!",
            "skill_type": "recipe",
            "description": "bad",
            "content": "nope",
        })
        assert "Error" in result


class TestDeleteSkillTool:
    def test_delete_existing(self, logger, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        skills_dir = tmp_path / ".pkm" / "skills"
        (skills_dir / "doomed.md").write_text("content")

        tool = DeleteSkillTool(logger, tmp_path)
        result = tool.execute({"skill_name": "doomed", "reason": "redundant"})
        assert "Deleted" in result
        assert not (skills_dir / "doomed.md").exists()

    def test_delete_nonexistent(self, logger, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        tool = DeleteSkillTool(logger, tmp_path)
        result = tool.execute({"skill_name": "nope", "reason": "test"})
        assert "not found" in result


class TestWriteMemoryTool:
    def test_append(self, logger, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        tool = WriteMemoryTool(logger, tmp_path)

        tool.execute({"category": "observations", "content": "First observation"})
        tool.execute({"category": "observations", "content": "Second observation"})

        content = read_memory_file("observations", tmp_path)
        assert "First observation" in content
        assert "Second observation" in content

    def test_replace(self, logger, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        tool = WriteMemoryTool(logger, tmp_path)

        tool.execute({"category": "plans", "content": "Old plan"})
        tool.execute({"category": "plans", "content": "New plan", "mode": "replace"})

        content = read_memory_file("plans", tmp_path)
        assert "Old plan" not in content
        assert "New plan" in content


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

from pkm_bridge.self_improvement.prompt import (
    build_budget_section,
    build_memory_section,
    build_run_context,
    build_system_prompt,
)


class TestPromptAssembly:
    def test_memory_section_empty(self, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        section = build_memory_section(tmp_path)
        assert "first run" in section.lower()

    def test_memory_section_with_content(self, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        write_memory_file("observations", "# My observations\n- Found a bug", tmp_path)

        section = build_memory_section(tmp_path)
        assert "observations" in section
        assert "Found a bug" in section

    def test_run_context(self):
        stats = {
            "days_since_last_run": 1,
            "queries_since_last_run": 42,
            "unprocessed_feedback": 5,
            "feedback_signals": {
                "retrieval_misses": 3,
                "user_corrections": 1,
                "positive": 10,
                "negative": 2,
            },
            "active_rules": 8,
            "total_skills": 4,
        }
        ctx = build_run_context(stats)
        assert "42" in ctx
        assert "8" in ctx

    def test_budget_section(self):
        b = Budget(max_turns=10, max_actions=5)
        section = build_budget_section(b)
        assert "10" in section
        assert "5" in section

    def test_full_prompt(self, tmp_path: Path):
        ensure_pkm_structure(tmp_path)
        b = Budget()
        prompt = build_system_prompt(tmp_path, b, {})
        assert "self-improvement agent" in prompt.lower()
        assert "budget" in prompt.lower()


# ---------------------------------------------------------------------------
# Agent loop (mocked API)
# ---------------------------------------------------------------------------

from pkm_bridge.self_improvement.agent import SelfImprovementAgent


class TestAgentLoop:
    def _make_mock_response(self, text: str = "Done.", stop_reason: str = "end_turn"):
        """Create a mock Claude API response."""
        mock_response = MagicMock()
        mock_response.stop_reason = stop_reason

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = text
        mock_response.content = [text_block]

        mock_usage = MagicMock()
        mock_usage.input_tokens = 1000
        mock_usage.output_tokens = 500
        mock_response.usage = mock_usage

        return mock_response

    def _make_tool_response(self, tool_name: str, tool_input: dict, tool_id: str = "tool_1"):
        """Create a mock response with a tool call."""
        mock_response = MagicMock()
        mock_response.stop_reason = "tool_use"

        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = tool_name
        tool_block.input = tool_input
        tool_block.id = tool_id

        mock_response.content = [tool_block]

        mock_usage = MagicMock()
        mock_usage.input_tokens = 500
        mock_usage.output_tokens = 200
        mock_response.usage = mock_usage

        return mock_response

    def test_single_turn_run(self, tmp_path: Path):
        """Agent makes one API call and gets a text response."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_mock_response("All looks good.")

        mock_config = MagicMock()
        mock_config.org_dir = str(tmp_path)
        ensure_pkm_structure(tmp_path)

        agent = SelfImprovementAgent(
            mock_client, logging.getLogger("test"), mock_config,
            max_turns=5, max_actions=3,
        )

        with patch("pkm_bridge.self_improvement.agent.gather_run_stats", return_value={}), \
             patch("pkm_bridge.self_improvement.agent.SelfImprovementAgent._save_run_to_db"):
            result = agent.run(trigger="manual")

        assert result["error"] is None
        assert result["trigger"] == "manual"
        assert "All looks good" in result["summary"]
        assert mock_client.messages.create.call_count == 1

    def test_tool_loop(self, tmp_path: Path):
        """Agent calls a tool, gets a result, then finishes."""
        mock_client = MagicMock()
        # First call: agent wants to read memory
        # Second call: agent finishes
        mock_client.messages.create.side_effect = [
            self._make_tool_response("read_memory", {}),
            self._make_mock_response("Reviewed memory, nothing to do."),
        ]

        mock_config = MagicMock()
        mock_config.org_dir = str(tmp_path)
        ensure_pkm_structure(tmp_path)

        agent = SelfImprovementAgent(
            mock_client, logging.getLogger("test"), mock_config,
            max_turns=5, max_actions=3,
        )

        with patch("pkm_bridge.self_improvement.agent.gather_run_stats", return_value={}), \
             patch("pkm_bridge.self_improvement.agent.SelfImprovementAgent._save_run_to_db"):
            result = agent.run()

        assert result["error"] is None
        assert mock_client.messages.create.call_count == 2

    def test_budget_stops_agent(self, tmp_path: Path):
        """Agent stops when turns budget is exhausted."""
        mock_client = MagicMock()
        # Always return tool calls — agent should stop after max_turns
        mock_client.messages.create.return_value = self._make_tool_response("read_memory", {})

        mock_config = MagicMock()
        mock_config.org_dir = str(tmp_path)
        ensure_pkm_structure(tmp_path)

        agent = SelfImprovementAgent(
            mock_client, logging.getLogger("test"), mock_config,
            max_turns=2, max_actions=3,
        )

        with patch("pkm_bridge.self_improvement.agent.gather_run_stats", return_value={}), \
             patch("pkm_bridge.self_improvement.agent.SelfImprovementAgent._save_run_to_db"):
            result = agent.run()

        assert result["error"] is None
        assert result["budget"]["turns"] == "2/2"

    def test_run_file_written(self, tmp_path: Path):
        """Agent writes a run log file to .pkm/runs/."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_mock_response("Done.")

        mock_config = MagicMock()
        mock_config.org_dir = str(tmp_path)
        ensure_pkm_structure(tmp_path)

        agent = SelfImprovementAgent(
            mock_client, logging.getLogger("test"), mock_config,
        )

        with patch("pkm_bridge.self_improvement.agent.gather_run_stats", return_value={}), \
             patch("pkm_bridge.self_improvement.agent.SelfImprovementAgent._save_run_to_db"):
            result = agent.run()

        runs_dir = tmp_path / ".pkm" / "runs"
        run_files = list(runs_dir.glob("*.md"))
        assert len(run_files) == 1
        content = run_files[0].read_text()
        assert "Self-Improvement Run" in content

    def test_action_budget_enforcement(self, tmp_path: Path):
        """Action tools are blocked when action budget is exhausted."""
        mock_client = MagicMock()
        # Agent tries write_memory (an action tool) twice, then finishes
        mock_client.messages.create.side_effect = [
            self._make_tool_response("write_memory", {"category": "observations", "content": "test"}, "t1"),
            self._make_tool_response("write_memory", {"category": "plans", "content": "test2"}, "t2"),
            self._make_mock_response("Done."),
        ]

        mock_config = MagicMock()
        mock_config.org_dir = str(tmp_path)
        ensure_pkm_structure(tmp_path)

        agent = SelfImprovementAgent(
            mock_client, logging.getLogger("test"), mock_config,
            max_turns=5, max_actions=1,  # Only 1 action allowed
        )

        with patch("pkm_bridge.self_improvement.agent.gather_run_stats", return_value={}), \
             patch("pkm_bridge.self_improvement.agent.SelfImprovementAgent._save_run_to_db"):
            result = agent.run()

        # First action succeeds, second should be blocked
        assert result["budget"]["actions"] == "1/1"


# ---------------------------------------------------------------------------
# Skills path migration (skills.py uses new path)
# ---------------------------------------------------------------------------

from pkm_bridge.tools.skills import _get_skills_dir


class TestSkillsPathMigration:
    def test_new_install_uses_pkm_skills(self, tmp_path: Path):
        """On a fresh install, _get_skills_dir creates .pkm/skills/."""
        skills_dir = _get_skills_dir(tmp_path)
        assert ".pkm" in str(skills_dir)
        assert skills_dir.name == "skills"
        assert skills_dir.is_dir()

    def test_existing_pkm_uses_new_path(self, tmp_path: Path):
        """When .pkm/skills/ exists, use it."""
        (tmp_path / ".pkm" / "skills").mkdir(parents=True)
        skills_dir = _get_skills_dir(tmp_path)
        assert skills_dir == tmp_path / ".pkm" / "skills"
